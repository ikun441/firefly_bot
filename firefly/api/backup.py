"""流萤 Firefly · 数据备份与还原接口。

放在设置页的「数据管理」分组下。三个动作对应三种用户诉求：
导出（我要留个底）、还原（我要退回去）、下载文件（我要拷走）。

安全上做了一层：**还原是破坏性操作**，
因此它要求请求体里显式带上 ``confirm: "RESTORE"``。
前端弹窗确认只能挡住误点，挡不住脚本直接打接口；
多一个必填字段让「误触还原」在协议层就不可能发生。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.session import get_session
from ..services import backup as backup_service
from ..services.audit import log_action
from .deps import current_user, fail, ok

router = APIRouter(tags=["backup"])

# 上传备份文件的大小上限：8 MB。
# 参考量级：本项目 13 张表全量 JSON 在 1 MB 内，
# 留 8 倍余量足够，也避免有人上传几百兆的文件把服务拖死。
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class RestoreIn(BaseModel):
    """还原请求。

    ``filename`` 与服务端已有的备份文件二选一：
    也可以直接上传文件（见 ``/backup/restore-upload``）。
    """

    filename: str
    confirm: str = ""


@router.get("/backup/list")
async def list_backups(
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """列出服务端备份目录里的文件。"""
    items = backup_service.list_backup_files()
    return ok(
        items,
        total=len(items),
        backup_dir=str(backup_service.BACKUP_DIR),
    )


@router.post("/backup/create")
async def create_backup(
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """立即生成一份备份文件。"""
    path = await backup_service.create_backup_file(
        session, note=f"手动备份（用户 {user.nickname or user.id}）"
    )
    # 顺手清理过期的自动安全备份，避免目录无限膨胀
    removed = backup_service.cleanup_auto_backups()

    await log_action(
        session,
        action="backup_create",
        user_id=user.id,
        target_type="backup",
        target_id=path.name,
        operator="user",
        detail=f"生成备份 {path.name}",
        channel="web",
    )
    await session.commit()

    stat = path.stat()
    return ok(
        {
            "name": path.name,
            "size": stat.st_size,
            "size_label": _human(stat.st_size),
            "path": str(path),
            "auto_backups_removed": removed,
        },
        message="备份已生成",
    )


@router.get("/backup/download")
async def download_backup(
    name: str = Query(..., description="备份文件名"),
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> FileResponse:
    """下载备份文件到本地。

    用 FileResponse 而不是自己读文件再返回：大文件不必整个进内存。
    ``filename=`` 走 Content-Disposition，浏览器会直接存盘而不是内联渲染 JSON。

    文件不存在时走 ``fail()``，与全项目一致地返回 200 + ``success: false``
    （见 deps.fail 的说明：业务失败不改 HTTP 状态码，前端判 success 字段）。
    """
    target_dir = backup_service.BACKUP_DIR.resolve()
    # Path(name).name 剥掉任何目录部分，因此 ../../../etc/passwd 会变成
    # 'passwd'，随后在备份目录里找不到 —— 路径穿越到此为止。
    safe_name = Path(name).name
    path = target_dir / safe_name
    if not path.exists() or not path.is_file():
        return fail(f"备份文件不存在：{safe_name}", code=404)  # type: ignore[return-value]

    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=safe_name,
    )


@router.post("/backup/inspect")
async def inspect_backup(
    file: UploadFile = File(...),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """只解析上传的备份文件并返回摘要，**不写库**。

    存在的意义与 CSV 导入的 ``dry_run`` 一样：还原是破坏性操作，
    先让用户看清「这份备份里有多少条流水、什么时候备的」，
    比直接覆盖后再解释要好得多。
    """
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return fail(
            f"备份文件过大（{_human(len(raw))}），上限 {_human(MAX_UPLOAD_BYTES)}",
            code=400,
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return fail("文件不是 UTF-8 文本，可能不是 JSON 备份", code=400)
    except json.JSONDecodeError as exc:
        return fail(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）", code=400)

    valid, message = backup_service.validate_backup(data)
    if not valid:
        return fail(message, code=400)

    summary = backup_service.summarize_backup(data)
    summary["valid"] = True
    summary["notice"] = message
    return ok(summary)


@router.post("/backup/restore")
async def restore_from_file(
    payload: RestoreIn,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """用服务端已有的备份文件还原。"""
    if payload.confirm != "RESTORE":
        return fail(
            "还原操作需要显式确认（confirm 字段应为 RESTORE）",
            code=400,
        )

    try:
        data = backup_service.read_backup_file(payload.filename)
    except FileNotFoundError as exc:
        return fail(str(exc), code=404)
    except ValueError as exc:
        return fail(str(exc), code=400)
    except Exception as exc:  # noqa: BLE001
        return fail(f"备份文件读取失败：{exc}", code=400)

    return await _do_restore(session, user, data, source=payload.filename)


@router.post("/backup/restore-upload")
async def restore_from_upload(
    file: UploadFile = File(...),
    confirm: str = Query("", description="必须为 RESTORE"),
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """上传备份文件并还原。"""
    if confirm != "RESTORE":
        return fail("还原操作需要显式确认（confirm=RESTORE）", code=400)

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return fail(
            f"备份文件过大（{_human(len(raw))}），上限 {_human(MAX_UPLOAD_BYTES)}",
            code=400,
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return fail(f"备份文件解析失败：{exc}", code=400)

    return await _do_restore(session, user, data, source=file.filename or "上传文件")


async def _do_restore(
    session: AsyncSession,
    user: Any,
    data: Dict[str, Any],
    *,
    source: str,
) -> Dict[str, Any]:
    """共用的还原流程：校验 → 安全备份 → 整表替换 → 审计。"""
    valid, message = backup_service.validate_backup(data)
    if not valid:
        return fail(message, code=400)

    try:
        result = await backup_service.restore_backup(session, data)
    except Exception as exc:  # noqa: BLE001
        # 还原失败必须整体回滚：半还原状态比不还原更糟，
        # 用户会看到一部分新数据一部分旧数据，且无从判断哪些没进来。
        await session.rollback()
        return fail(f"还原失败，已回滚到操作前的状态：{exc}", code=500)

    await log_action(
        session,
        action="backup_restore",
        user_id=user.id,
        target_type="backup",
        target_id=source,
        operator="user",
        detail=f"从 {source} 还原，恢复 {result['total_rows']} 行",
        extra=result,
        channel="web",
    )
    await session.commit()

    return ok(
        result,
        message=f"已从备份还原 {result['total_rows']} 行数据",
    )


@router.delete("/backup/{name}")
async def remove_backup(
    name: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """删除一个备份文件。"""
    try:
        removed = backup_service.delete_backup_file(name)
    except ValueError as exc:
        return fail(str(exc), code=400)

    if not removed:
        return fail("备份文件不存在", code=404)

    await log_action(
        session,
        action="backup_delete",
        user_id=user.id,
        target_type="backup",
        target_id=name,
        operator="user",
        detail=f"删除备份 {name}",
        channel="web",
    )
    await session.commit()
    return ok({"name": name, "deleted": True})


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"
