"""预算管理接口。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import audit as audit_service
from ..services import budget as budget_service
from ..services import ledger as ledger_service
from .deps import MAX_AMOUNT, fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["budget"])


def _as_int(value: Union[int, str, None]) -> Optional[int]:
    """把可选的 id 归一化成 int；空串 / None / 非法值都返回 None。

    与 firefly/api/transaction.py 的同名助手保持一致：
    前端有时传数字、有时传字符串，不归一化会让主键列混存两种类型。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


class BudgetIn(BaseModel):
    category_id: Optional[Union[int, str]] = None
    # 只卡上界，**不要**在这里加 gt=0。
    #
    # 负数金额由 budget_service 里的 `if amount <= 0` 拦截，走的是
    # HTTP 200 + success:false 的**业务错误**信封；一旦写成
    # Field(gt=0)，负数会提前被 pydantic 判为字段校验失败，
    # 变成裸 422 {detail:[...]}（没有 success 字段），
    # 与全项目「业务规则错误用信封」的约定冲突，前端也拿不到提示文案。
    amount: float = Field(..., le=MAX_AMOUNT)
    period: str = "monthly"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    alert_enabled: bool = True


@router.get("/budgets")
async def list_budgets(
    session: AsyncSession = Depends(get_session),
    period: str = Query(default="monthly"),
) -> Dict[str, Any]:
    """预算列表 + 当前周期执行情况。"""
    user = await get_or_create_user(session)

    start, end = ledger_service.period_bounds(period)
    status = await budget_service.get_budget_status(
        session, user_id=user.id, start=start, end=end, period=period
    )

    return ok(status.get("items", []), summary=status)


@router.post("/budgets")
async def create_budget(
    payload: BudgetIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)

    if payload.amount <= 0:
        return fail("预算金额必须大于 0")

    # 与流水接口同样的归一化：前端可能传 "3" 也可能传 3，
    # 不统一会往 SQLite 里混存字符串与整数两种主键。
    category_id = _as_int(payload.category_id)

    budget = await budget_service.create_budget(
        session,
        user_id=user.id,
        category_id=category_id,
        amount=payload.amount,
        period=payload.period,
        start_date=parse_dt(payload.start_date),
        # 预算的作用域只由 period + start_date 决定，
        # 服务层没有 end_date 参数（结束日期由周期自动推算）。
        alert_enabled=payload.alert_enabled,
    )

    await audit_service.log_action(
        session,
        "create_budget",
        user_id=user.id,
        target_type="budget",
        target_id=budget.id,
        operator="user",
        detail=f"设置预算 {payload.amount} 元（{payload.period}）",
        channel="web",
    )
    return ok(to_dict(budget))


@router.delete("/budgets/{budget_id}")
async def delete_budget(
    budget_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)
    deleted = await budget_service.delete_budget(session, budget_id, user_id=user.id)
    if not deleted:
        return fail("预算不存在", code=404)

    await audit_service.log_action(
        session,
        "delete_budget",
        user_id=user.id,
        target_type="budget",
        target_id=budget_id,
        operator="user",
        detail="删除预算",
        channel="web",
    )
    return ok({"id": budget_id, "deleted": True})
