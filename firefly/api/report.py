"""报告生成、归档与推送接口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Report, SuggestionKind
from ..db.session import get_session
from ..services import audit as audit_service
from ..services import ledger as ledger_service
from ..services import report as report_service
from .deps import VALID_PERIODS, fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["report"])

# 与 firefly/services/ledger.period_bounds 支持的周期保持一致。
# 在这里列成常量而不是校验时才去 try/except，是为了让错误信息
# 能直接告诉用户「可选哪些值」，而不是一句笼统的失败。


class GenerateIn(BaseModel):
    period: str = "monthly"
    reference: Optional[str] = None
    custom_days: Optional[int] = None
    custom_start: Optional[str] = None
    custom_end: Optional[str] = None
    push: bool = False


@router.post("/reports/generate")
async def generate_report(
    payload: GenerateIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """生成周期报告（含财务税务整体状况 + 财务税务优化方向）。"""
    user = await get_or_create_user(session)

    # 周期是用户输入，必须在入口校验。
    # period_bounds 对未知周期抛 ValueError，如果放任它穿到这里，
    # 用户看到的是 500 内部错误——但那其实是「参数不合法」，应当是 400。
    if payload.period not in VALID_PERIODS:
        return fail(
            f"不支持的周期类型：{payload.period}，可选 {sorted(VALID_PERIODS)}",
            code=400,
        )

    report = await report_service.generate_report(
        session,
        user_id=user.id,
        period=payload.period,
        reference=parse_dt(payload.reference),
        custom_days=payload.custom_days,
        custom_start=parse_dt(payload.custom_start),
        custom_end=parse_dt(payload.custom_end),
        persist=True,
        channel="web",
    )

    if report.status.value == "failed" if hasattr(report.status, "value") else False:
        return fail(f"报告生成失败：{report.error_message or '未知错误'}", code=500)

    result = to_dict(report)

    # 可选：立即推送
    if payload.push:
        from ..render.push import push_report

        push_result = await push_report(report=report)
        result["push"] = push_result

    return ok(result)


@router.get("/reports/latest")
async def latest_report(
    session: AsyncSession = Depends(get_session),
    period: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """最近一次成功生成的报告（前端首屏直接展示）。"""
    user = await get_or_create_user(session)
    report = await report_service.get_latest_report(session, user_id=user.id, period=period)
    if report is None:
        return ok(None, message="尚无报告")
    return ok(to_dict(report))


@router.get("/reports")
async def list_reports(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """报告归档列表（用于历史对比）。"""
    user = await get_or_create_user(session)
    reports = await report_service.list_reports(session, user_id=user.id, limit=limit)

    rows: List[Dict[str, Any]] = []
    for report in reports:
        row = to_dict(report, exclude={"payload", "summary_text"})
        payload = report.payload or {}
        overview = payload.get("overview", {}) if isinstance(payload, dict) else {}
        row["income"] = overview.get("income")
        row["expense"] = overview.get("expense")
        row["net"] = overview.get("net")
        rows.append(row)

    return ok(rows, total=len(rows))


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """按 ID 读取完整报告（含 payload）。"""
    user = await get_or_create_user(session)
    report = await report_service.get_report(session, report_id, user_id=user.id)
    if report is None:
        return fail("报告不存在", code=404)
    return ok(to_dict(report))


class PushOptions(BaseModel):
    """推送选项。

    全部可选：不传时用 config.yaml 的 push 分组兜底，
    这样「直接点按钮」和「定时推送」走同一套默认值。
    """

    content_type: Optional[str] = Field(
        default=None,
        description="推送形态：text（纯文字）/ image（图片）/ both（两者都发）",
    )
    ai_summary: Optional[bool] = Field(
        default=None,
        description="是否用大模型生成一段解读，追加在规则摘要之后",
    )
    ai_temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0,
        description="AI 总结的采样温度，越高措辞越灵活",
    )
    ai_max_tokens: Optional[int] = Field(
        default=None, ge=64, le=8000,
        description="AI 总结的输出长度上限",
    )
    target_type: Optional[str] = Field(default=None, description="private / group")
    target_id: Optional[str] = Field(default=None, description="覆盖默认推送目标")


@router.post("/reports/{report_id}/push")
async def push_report_api(
    report_id: str,
    payload: PushOptions = PushOptions(),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """把已有报告渲染成图片/文字并通过协议端推送。

    返回体里的 ``text_sent`` / ``image_sent`` 才是真实投递结果 ——
    HTTP 200 只代表请求被受理，协议端连不上时这两个值仍是 false。
    前端的提示文案必须依据它们，否则会出现「提示成功但没收到」。
    """
    user = await get_or_create_user(session)
    report = await report_service.get_report(session, report_id, user_id=user.id)
    if report is None:
        return fail("报告不存在", code=404)

    from ..render.push import push_report

    result = await push_report(
        report=report,
        session=session,
        content_type=payload.content_type,
        ai_summary=payload.ai_summary,
        ai_temperature=payload.ai_temperature,
        ai_max_tokens=payload.ai_max_tokens,
        target_type=payload.target_type,
        target_id=payload.target_id,
    )
    return ok(result)


@router.get("/reports/{report_id}/preview")
async def preview_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """渲染报告静态 HTML（返回文件路径，用于前端 iframe 预览）。"""
    user = await get_or_create_user(session)
    report = await report_service.get_report(session, report_id, user_id=user.id)
    if report is None:
        return fail("报告不存在", code=404)

    from ..render.html import render_report_html

    path = await render_report_html(report)
    report.html_path = str(path)
    session.add(report)
    await session.commit()

    return ok({"html_path": str(path), "url": "/api/reports/" + report_id + "/html"})


@router.get("/reports/{report_id}/html")
async def report_html(
    report_id: str,
    session: AsyncSession = Depends(get_session),
):
    """直接返回渲染好的 HTML 内容。"""
    from fastapi.responses import HTMLResponse

    user = await get_or_create_user(session)
    report = await report_service.get_report(session, report_id, user_id=user.id)
    if report is None:
        return HTMLResponse("<h1>报告不存在</h1>", status_code=404)

    from ..render.html import build_report_html

    html = build_report_html(report)
    return HTMLResponse(html)
