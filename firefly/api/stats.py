"""总览统计与异常检测接口。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import anomaly as anomaly_service
from ..services import attribution as attribution_service
from ..services import budget as budget_service
from ..services import ledger as ledger_service
from .deps import VALID_PERIODS, get_or_create_user, ok, parse_dt

router = APIRouter(tags=["stats"])




@router.get("/stats")
async def stats(
    session: AsyncSession = Depends(get_session),
    period: str = Query(default="monthly"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    custom_days: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    """首页总览：收支概览 + 类别结构 + 近月趋势 + 账户余额。"""
    user = await get_or_create_user(session)

    # period 来自查询参数，非法值会在 period_bounds 里抛 ValueError → 500。
    # 这本质是「参数不合法」，应当明确回 400 而不是假装服务器出错。
    if period not in VALID_PERIODS:
        return fail(
            f"不支持的周期类型：{period}，可选 {sorted(VALID_PERIODS)}",
            code=400,
        )

    period_start, period_end = ledger_service.period_bounds(
        period,
        reference=parse_dt(start) if start else None,
        custom_days=custom_days,
    )
    if start:
        period_start = parse_dt(start)
    if end:
        period_end = parse_dt(end)

    prev_start, prev_end = ledger_service.previous_period_bounds(period, period_start, period_end)

    summary = await ledger_service.sum_by_direction(
        session, user_id=user.id, start=period_start, end=period_end
    )
    previous = await ledger_service.sum_by_direction(
        session, user_id=user.id, start=prev_start, end=prev_end
    )

    by_category = await ledger_service.aggregate_by_category(
        session, user_id=user.id, start=period_start, end=period_end, direction="expense", top_n=8
    )
    by_day = await ledger_service.aggregate_by_day(
        session, user_id=user.id, start=period_start, end=period_end
    )
    trend = await attribution_service.build_trend(session, user_id=user.id, months=6)
    accounts = await ledger_service.get_account_balances(session, user_id=user.id)
    anomaly_stats = await anomaly_service.get_anomaly_stats(
        session, user_id=user.id, start=period_start, end=period_end
    )

    def delta(current: float, before: float) -> Dict[str, Any]:
        diff = round(current - before, 2)
        ratio = round(diff / before * 100, 2) if before else None
        return {
            "value": diff,
            "ratio": ratio,
            "direction": "up" if diff > 0 else "down" if diff < 0 else "flat",
        }

    return ok(
        {
            "period": period,
            "period_label": ledger_service.format_period_label(period, period_start, period_end),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "summary": summary,
            "previous": previous,
            "compare": {
                "income": delta(summary["income"], previous["income"]),
                "expense": delta(summary["expense"], previous["expense"]),
                "net": delta(summary["net"], previous["net"]),
            },
            "by_category": by_category,
            "by_day": by_day,
            "trend": trend,
            "accounts": accounts,
            "anomaly": anomaly_stats,
        }
    )


@router.get("/anomalies")
async def list_anomalies(
    session: AsyncSession = Depends(get_session),
    period: str = Query(default="monthly"),
    mode: str = Query(default="all", description="all / anomaly / personal"),
) -> Dict[str, Any]:
    """周期内的异常流水与被标记的个人消费混入。"""
    user = await get_or_create_user(session)
    start, end = ledger_service.period_bounds(period)

    if mode == "all":
        report = await anomaly_service.scan_period(
            session, user_id=user.id, start=start, end=end, create_suggestions=False
        )
    elif mode == "personal":
        full = await anomaly_service.scan_period(
            session, user_id=user.id, start=start, end=end, create_suggestions=False
        )
        report = {"scanned_count": full["scanned_count"], "anomalies": full["personal_mixed"]}
    else:
        full = await anomaly_service.scan_period(
            session, user_id=user.id, start=start, end=end, create_suggestions=False
        )
        report = {"scanned_count": full["scanned_count"], "anomalies": full["anomalies"]}

    duplicates = await anomaly_service.scan_duplicates(session, user_id=user.id, start=start, end=end)

    return ok({"report": report, "duplicates": duplicates})


@router.get("/budget-status")
async def budget_status(
    session: AsyncSession = Depends(get_session),
    period: str = Query(default="monthly"),
) -> Dict[str, Any]:
    """预算执行情况（总览页进度条数据源）。"""
    user = await get_or_create_user(session)

    # period 来自查询参数，非法值会在 period_bounds 里抛 ValueError → 500。
    # 这本质是「参数不合法」，应当明确回 400 而不是假装服务器出错。
    if period not in VALID_PERIODS:
        return fail(
            f"不支持的周期类型：{period}，可选 {sorted(VALID_PERIODS)}",
            code=400,
        )
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from ..config import get_config
    start, end = ledger_service.period_bounds(period, reference=datetime.now(ZoneInfo(get_config().app.timezone)))
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    status = await budget_service.get_budget_status(
        session, user_id=user.id, start=start, end=end, period=period
    )
    return ok(status)
