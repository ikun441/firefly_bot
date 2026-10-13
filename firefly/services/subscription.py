"""周期订阅：房租、宽带、订阅制服务的自动入账。

用户定义一次「每月 1 号扣 3500 房租」，之后每到该日期就自动生成一条流水，
并由调度器在每天固定时间检查。核心是 ``next_run_date`` 的推进逻辑 ——
它必须幂等，重复执行不会重复入账。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..config import get_config
from ..db.models import Subscription, TransactionDirection, TransactionSource
from . import ledger as ledger_service
from . import audit as audit_service

PERIOD_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}


def _today() -> date:
    return datetime.now().date()


def compute_next_run(
    *,
    period: str,
    day_of_period: int,
    start_date: Optional[date] = None,
    after: Optional[date] = None,
) -> date:
    """计算下一次应当入账的日期。

    对 monthly：取当月的 ``day_of_period`` 号；若已过则顺延到次月。
    对 weekly：``day_of_period`` 表示星期几（0=周一）。
    对 yearly：``day_of_period`` 表示月内日，配合 start_date 的月份。
    """
    base = after or _today()
    start = start_date or _today()

    if period == "weekly":
        target_weekday = max(0, min(6, day_of_period - 1)) if day_of_period >= 1 else 0
        days_ahead = (target_weekday - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # 今天已执行过，下一次是下周
        return base + timedelta(days=days_ahead)

    if period == "monthly":
        day = max(1, min(28, day_of_period or 1))
        candidate = base.replace(day=day)
        if candidate <= base:
            year = base.year + (1 if base.month == 12 else 0)
            month = 1 if base.month == 12 else base.month + 1
            candidate = date(year, month, day)
        return candidate

    if period == "quarterly":
        day = max(1, min(28, day_of_period or 1))
        # 从起始月按季度推进
        year, month = base.year, base.month
        while True:
            candidate = date(year, month, day)
            if candidate > base:
                return candidate
            month += 3
            if month > 12:
                month -= 12
                year += 1

    if period == "yearly":
        day = max(1, min(28, day_of_period or 1))
        candidate = date(base.year, start.month, day)
        if candidate <= base:
            candidate = date(base.year + 1, start.month, day)
        return candidate

    return base + timedelta(days=30)


async def create_subscription(
    session: AsyncSession,
    *,
    user_id: str,
    name: str,
    amount: float,
    period: str = "monthly",
    day_of_period: int = 1,
    start_date: Optional[datetime] = None,
    category_id: Optional[str] = None,
    account_id: Optional[str] = None,
    memo: str = "",
) -> Subscription:
    start = (start_date or datetime.now()).date()
    next_run = compute_next_run(
        period=period, day_of_period=day_of_period, start_date=start, after=start - timedelta(days=1)
    )

    subscription = Subscription(
        user_id=user_id,
        name=name,
        amount=abs(amount),
        period=period,
        day_of_period=day_of_period,
        start_date=start,
        category_id=category_id,
        account_id=account_id,
        memo=memo,
        next_run_date=next_run,
        is_active=True,
    )

    if category_id is None:
        # 尝试按名称匹配一个类别
        categories = await ledger_service.list_categories(session, user_id=user_id)
        for category in categories:
            if category.name and category.name in name:
                subscription.category_id = category.id
                break

    session.add(subscription)
    await session.commit()
    await session.refresh(subscription)

    await audit_service.log_action(
        session,
        "create_subscription",
        user_id=user_id,
        target_type="subscription",
        target_id=subscription.id,
        operator="user",
        detail=f"新增周期订阅「{name}」{amount:.2f} 元 / {period}",
        channel="web",
    )
    return subscription


async def list_subscriptions(
    session: AsyncSession,
    *,
    user_id: str,
    only_active: bool = False,
) -> List[Subscription]:
    statement = select(Subscription).where(Subscription.user_id == user_id)
    if only_active:
        statement = statement.where(Subscription.is_active == True)  # noqa: E712
    statement = statement.order_by(Subscription.created_at.desc())  # type: ignore[attr-defined]
    return list((await session.execute(statement)).scalars().all())


async def delete_subscription(
    session: AsyncSession,
    subscription_id: str,
    *,
    user_id: Optional[str] = None,
) -> bool:
    statement = select(Subscription).where(Subscription.id == subscription_id)
    if user_id:
        statement = statement.where(Subscription.user_id == user_id)

    subscription = (await session.execute(statement)).scalars().first()
    if subscription is None:
        return False

    await session.delete(subscription)
    await session.commit()
    return True


async def run_due_subscriptions(
    session: AsyncSession,
    *,
    today: Optional[date] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """把所有到期的订阅转成真实流水，并推进 ``next_run_date``。

    幂等保证：处理完一条就把 ``next_run_date`` 推到下一个未来日期，
    所以同一天重复调用不会重复入账。
    """
    current = today or _today()

    statement = select(Subscription).where(
        Subscription.is_active == True,  # noqa: E712
        Subscription.next_run_date <= current,
    )
    if user_id:
        statement = statement.where(Subscription.user_id == user_id)

    due = list((await session.execute(statement)).scalars().all())

    created: List[Dict[str, Any]] = []

    for subscription in due:
        if subscription.end_date and current > subscription.end_date:
            subscription.is_active = False
            session.add(subscription)
            continue

        transaction = await ledger_service.create_transaction(
            session,
            user_id=subscription.user_id,
            amount=subscription.amount,
            direction=TransactionDirection.EXPENSE,
            category_id=subscription.category_id,
            account_id=subscription.account_id,
            trade_time=datetime.combine(current, datetime.min.time()).replace(hour=9),
            counterparty=subscription.name,
            memo=subscription.memo or f"周期订阅自动入账：{subscription.name}",
            source=TransactionSource.SUBSCRIPTION,
            is_confirmed=True,
        )

        # 推进到下一个未来日期
        subscription.next_run_date = compute_next_run(
            period=subscription.period,
            day_of_period=subscription.day_of_period,
            start_date=subscription.start_date,
            after=current,
        )
        session.add(subscription)

        created.append(
            {
                "subscription_id": subscription.id,
                "name": subscription.name,
                "amount": subscription.amount,
                "transaction_id": transaction.id,
                "next_run_date": subscription.next_run_date.isoformat(),
            }
        )

        await audit_service.log_action(
            session,
            "subscription_auto_entry",
            user_id=subscription.user_id,
            target_type="subscription",
            target_id=subscription.id,
            operator="scheduler",
            detail=f"自动入账「{subscription.name}」{subscription.amount:.2f} 元",
            channel="web",
        )

    if due:
        await session.commit()

    return {"processed": len(due), "created": created, "date": current.isoformat()}


def scheduler_status() -> Dict[str, Any]:
    """返回定时推送订阅的当前配置（供前端设置页展示）。"""
    config = get_config()
    return {
        "push_enabled": config.push.enabled,
        "push_target_type": config.push.target_type,
        "push_target_id": config.push.target_id,
        "push_schedule_times": config.push.schedule_times,
        "push_report_period": config.push.report_period,
        "subscription_enabled": config.subscription.enabled,
        "subscription_run_day": config.subscription.run_day,
        "subscription_run_time": config.subscription.run_time,
        "alert_enabled": config.alert.enabled,
        "daily_expense_limit": config.alert.daily_expense_limit,
        "low_balance_threshold": config.alert.low_balance_threshold,
    }


__all__ = [
    "create_subscription",
    "list_subscriptions",
    "delete_subscription",
    "run_due_subscriptions",
    "compute_next_run",
    "scheduler_status",
]
