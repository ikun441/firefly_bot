"""账单批量导入接口（P2 · 2-1）：CSV / 截图 OCR。

两条入口共用同一条流水线：
解析 → 去重 → 入库（或进待确认）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import audit as audit_service
from ..services import importer
from .deps import fail, get_or_create_user, ok, to_dict

router = APIRouter(tags=["import"])


@router.get("/import/excel/template")
async def excel_template():
    from ..config import INDEX_DIR
    return FileResponse(INDEX_DIR / "assets" / "templates" / "firefly-import.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename="Firefly-import-template.xlsx")


@router.post("/import/excel")
async def import_excel(
    file: UploadFile = File(...),
    account_id: Optional[int] = Form(default=None),
    dry_run: bool = Query(default=False),
    dry_run_form: Optional[bool] = Form(default=None, alias="dry_run"),
    session: AsyncSession = Depends(get_session),
):
    from ..services import excel_import
    if dry_run_form is not None:
        dry_run = dry_run_form
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".xlsx"):
        return fail("仅支持 .xlsx 文件，请将旧版 .xls 另存为 .xlsx")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if error := _check_size(content, filename):
        return fail(error)
    try:
        items = await asyncio.to_thread(excel_import.parse_excel, content)
        user = await get_or_create_user(session)
        items = await excel_import.resolve_items(session, user_id=user.id, items=items, account_id=account_id)
        if dry_run:
            return ok({"dry_run": True, "total": len(items), "preview": items[:50], "message": "校验通过，尚未入账，预览不发送通知"})
        result = await excel_import.import_excel_items(session, user_id=user.id, items=items, filename=filename)
        await audit_service.log_action(session, "import_excel", user_id=user.id,
                                       target_type="import_batch", target_id=result["batch_id"],
                                       operator="user", detail=result["message"], channel="web")
        # Notify only after the whole batch and its audit record are durable.
        await session.commit()
    except excel_import.ExcelValidationError as exc:
        await session.rollback()
        return fail(str(exc), detail={"errors": exc.errors[:100]})
    except ValueError as exc:
        await session.rollback()
        return fail(str(exc))
    except Exception:
        await session.rollback()
        return fail("导入未完成，本批次已回滚；请检查文件格式和后端日志")
    result["notification"] = await excel_import.notify_import(result)
    return ok(result)

# 允许的导入文件类型与体积上限
ALLOWED_CSV_EXT = (".csv", ".txt")
ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _check_size(content: bytes, filename: str) -> Optional[str]:
    """返回错误信息；None 表示通过。"""
    if not content:
        return "文件内容为空"
    if len(content) > MAX_UPLOAD_BYTES:
        return f"文件过大（{len(content) / 1024 / 1024:.1f}MB），请拆分后重试（上限 8MB）"
    return None


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    auto_confirm: bool = Form(default=True),
    dry_run: bool = Query(
        default=False,
        description="true 时只解析不入库（预览）。也接受同名表单字段。",
    ),
    dry_run_form: Optional[bool] = Form(default=None, alias="dry_run"),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """导入微信/支付宝账单 CSV。

    ``dry_run=true`` 时只解析不入库，供前端先做预览确认 ——
    导错账要一条条删，预览成本远低于回滚成本。

    关于 dry_run 为什么同时声明 Query 与 Form：
        它原先只写成 ``Form(default=False)``，于是**只有** multipart 表单字段
        能触发预览，放在查询串里的 ``?dry_run=true`` 会被静默忽略 ——
        接口照常执行真实入库，返回的还是普通的导入结果，
        调用方以为自己在预览，实际已经写库了。
        现在两种传法都认：表单字段优先（前端 FormData 的用法），
        查询参数兜底（curl / 脚本的直觉用法）。
    """
    # 表单字段显式传了就听它的，否则用查询参数
    if dry_run_form is not None:
        dry_run = dry_run_form
    user = await get_or_create_user(session)

    filename = (file.filename or "bill.csv").strip()
    if not filename.lower().endswith(ALLOWED_CSV_EXT):
        return fail(f"仅支持 {'/'.join(ALLOWED_CSV_EXT)} 文件，当前：{filename}")

    content = await file.read()
    if err := _check_size(content, filename):
        return fail(err)

    try:
        items, meta = importer.parse_csv(content)
    except Exception as exc:  # noqa: BLE001 - 解析失败要给出可读原因
        return fail(f"CSV 解析失败：{exc}")

    if not items:
        return fail(
            "未从文件中解析出任何账目，请确认是微信/支付宝导出的账单 CSV"
            f"（识别到的列：{meta.get('mapping') or '无'}）"
        )

    if dry_run:
        return ok(
            {
                "dry_run": True,
                "total": len(items),
                "preview": items[:50],
                "meta": meta,
                "message": f"解析出 {len(items)} 条，尚未入库",
            }
        )

    result = await importer.import_items(
        session,
        user_id=user.id,
        items=items,
        source_type="csv",
        filename=filename,
        auto_confirm=auto_confirm,
        meta=meta,
    )

    await audit_service.log_action(
        session,
        "import_csv",
        user_id=user.id,
        target_type="import_batch",
        target_id=result.get("batch_id"),
        operator="user",
        detail=result.get("message", ""),
        extra={"filename": filename, "total": result.get("total")},
        channel="web",
    )
    return ok(result)


@router.post("/import/ocr")
async def import_ocr(
    file: UploadFile = File(...),
    auto_confirm: bool = Form(default=False),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """上传账单截图，OCR 识别后解析成条目。

    默认 ``auto_confirm=False``：图像识别准确率天然低于 CSV，
    直接入库会污染账本，因此先给用户核对。
    """
    user = await get_or_create_user(session)

    filename = (file.filename or "shot.png").strip()
    if not filename.lower().endswith(ALLOWED_IMAGE_EXT):
        return fail(f"仅支持图片文件 {'/'.join(ALLOWED_IMAGE_EXT)}，当前：{filename}")

    content = await file.read()
    if err := _check_size(content, filename):
        return fail(err)

    ocr = await importer.run_ocr(content, filename=filename)
    if not ocr.get("success"):
        return fail(ocr.get("message") or "OCR 识别失败", detail={"lines": ocr.get("lines")})

    lines: List[str] = ocr.get("lines") or []
    text = ocr.get("text") or "\n".join(lines)

    items = importer.parse_ocr_text(text)
    if not items:
        return fail(
            "未能从截图中解析出账目，请换一张更清晰的账单截图",
            detail={"ocr_lines": lines[:20]},
        )

    result = await importer.import_items(
        session,
        user_id=user.id,
        items=items,
        source_type="ocr",
        filename=filename,
        auto_confirm=auto_confirm,
        meta={"ocr_lines": len(lines)},
    )
    result["ocr_line_count"] = len(lines)

    await audit_service.log_action(
        session,
        "import_ocr",
        user_id=user.id,
        target_type="import_batch",
        target_id=result.get("batch_id"),
        operator="user",
        detail=result.get("message", ""),
        extra={"filename": filename, "ocr_lines": len(lines)},
        channel="web",
    )
    return ok(result)


@router.post("/import/reparse")
async def reparse_text(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """直接对一段账单文本做解析（不落库）。

    给「粘贴账单文本」这种最轻量的入口用，也方便答辩现场
    不依赖文件就能演示解析能力。
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        return fail("请提供要解析的账单文本")

    items = importer.parse_ocr_text(text)
    return ok(
        {
            "total": len(items),
            "preview": items[:50],
            "message": f"解析出 {len(items)} 条",
        }
    )


@router.get("/import/batches")
async def list_batches(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """查看历史导入批次（导入了多少、去重了多少）。"""
    user = await get_or_create_user(session)
    batches = await importer.list_batches(session, user_id=user.id, limit=limit)
    return ok([to_dict(b) for b in batches], total=len(batches))
