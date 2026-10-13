"""税务风险自检接口。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import ledger as ledger_service
from ..services import taxcheck as taxcheck_service
from .deps import VALID_PERIODS, fail, get_or_create_user, ok, parse_dt

router = APIRouter(tags=["taxcheck"])


@router.post("/tax-check")
async def run_tax_check(
    session: AsyncSession = Depends(get_session),
    period: str = Query(default="monthly"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """运行税务风险自检，返回八项检查结论与优化方向。"""
    user = await get_or_create_user(session)

    # period 来自查询参数，非法值会在 period_bounds 里抛 ValueError → 500。
    # 这本质是「参数不合法」，应当明确回 400 而不是假装服务器出错。
    if period not in VALID_PERIODS:
        return fail(
            f"不支持的周期类型：{period}，可选 {sorted(VALID_PERIODS)}",
            code=400,
        )

    period_start, period_end = ledger_service.period_bounds(period)
    if start:
        period_start = parse_dt(start)
    if end:
        period_end = parse_dt(end)

    result = await taxcheck_service.run_tax_check(
        session,
        user_id=user.id,
        start=period_start,
        end=period_end,
        persist=True,
        create_suggestions=True,
    )
    return ok(result)


@router.get("/tax-check/checks")
async def list_checks(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """历史税检结论列表。"""
    from sqlmodel import select

    from ..db.models import TaxCheckResult

    user = await get_or_create_user(session)
    rows = (
        await session.execute(
            select(TaxCheckResult)
            .where(TaxCheckResult.user_id == user.id)
            .order_by(TaxCheckResult.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
    ).scalars().all()

    from .deps import to_dict as _to_dict

    return ok([_to_dict(r) for r in rows])


@router.get("/tax-check/policy")
async def policy_basis() -> Dict[str, Any]:
    """返回当前内置的政策依据文本（答辩时可解释判定口径）。"""
    return ok(taxcheck_service.POLICY_BASIS)
