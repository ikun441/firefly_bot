"""订阅自动入账与定时推送的配置接口。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import ledger as ledger_service
from ..services import subscription as subscription_service
from .deps import MAX_AMOUNT, fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["subscription"])


class SubscriptionIn(BaseModel):
    name: str
    # 只卡上界，理由同 budget.py：负数由本文件 payload.amount <= 0
    # 那句业务校验拦截，保持「业务错误走 200 + success:false」的约定。
    #
    # 订阅会按周期**自动入账**，上界尤其重要：
    # 一个手误的超大金额会被周期性重复写入，污染面随时间累积。
    amount: float = Field(..., le=MAX_AMOUNT)
    period: str = "monthly"
    day_of_period: int = 1
    start_date: str = ""
    category_id: str = ""
    account_id: str = ""
    memo: str = ""


class PushScheduleIn(BaseModel):
    enabled: bool
    target_type: str = ""
    target_id: str = ""
    schedule_times: list = []


@router.get("/subscriptions")
async def list_subscriptions(
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """周期订阅列表（房租、宽带等自动入账项）。"""
    user = await get_or_create_user(session)
    rows = await subscription_service.list_subscriptions(session, user_id=user.id)
    return ok([to_dict(row) for row in rows], total=len(rows))


@router.post("/subscriptions")
async def create_subscription(
    payload: SubscriptionIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)

    if payload.amount <= 0:
        return fail("金额必须大于 0")

    row = await subscription_service.create_subscription(
        session,
        user_id=user.id,
        name=payload.name,
        amount=payload.amount,
        period=payload.period,
        day_of_period=payload.day_of_period,
        start_date=parse_dt(payload.start_date),
        category_id=payload.category_id or None,
        account_id=payload.account_id or None,
        memo=payload.memo,
    )
    return ok(to_dict(row))


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)
    deleted = await subscription_service.delete_subscription(session, sub_id, user_id=user.id)
    if not deleted:
        return fail("订阅不存在", code=404)
    return ok({"id": sub_id, "deleted": True})


@router.get("/subscription/status")
async def subscription_status() -> Dict[str, Any]:
    """定时推送订阅的总状态（前端「设置」页展示）。"""
    return ok(subscription_service.scheduler_status())


@router.put("/subscription/status")
async def update_subscription_status(payload: PushScheduleIn) -> Dict[str, Any]:
    """更新定时推送配置（写回 config.yaml 并重排调度器任务）。"""
    from ..config import update_config

    patch: Dict[str, Any] = {"push.enabled": payload.enabled}
    if payload.target_type:
        patch["push.target_type"] = payload.target_type
    if payload.target_id:
        patch["push.target_id"] = payload.target_id
    if payload.schedule_times:
        patch["push.schedule_times"] = payload.schedule_times

    result = update_config(patch)

    from ..scheduler.tasks import reschedule

    reschedule()

    return ok({"updated": result.get("updated", []), "rejected": result.get("rejected", []), "status": subscription_service.scheduler_status()})
