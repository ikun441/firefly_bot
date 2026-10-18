"""Collect threshold alerts from committed local-ledger data, without AI calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlmodel import select

from ..config import get_config
from ..db.models import Budget, Category, TransactionDirection
from . import ledger


@dataclass(frozen=True)
class Alert:
    identity: str
    level: int
    text: str


async def collect_alerts(session, *, user_id: int, now: datetime) -> list[Alert]:
    config = get_config()
    local = now.astimezone(ZoneInfo(config.app.timezone))
    today = local.date()
    alerts = []
    day_start, day_end = ledger.period_bounds("daily", reference=local)
    daily = await ledger.sum_by_direction(
        session, user_id=user_id,
        start=day_start.astimezone(timezone.utc), end=day_end.astimezone(timezone.utc),
    )
    if config.alert.daily_expense_limit > 0 and daily["expense"] > config.alert.daily_expense_limit:
        alerts.append(Alert("daily_expense", 1,
            f"⚠️ 今日支出已达 {daily['expense']:.2f} 元，超过单日上限 {config.alert.daily_expense_limit:.2f} 元。"))

    if config.budget.enabled:
        # A newer configuration replaces the same category/period scope; disabled
        # or future budgets must not resurrect an older enabled configuration.
        rows = (await session.exec(select(Budget).where(Budget.user_id == user_id))).all()
        latest = {}
        for budget in sorted(rows, key=lambda b: (b.start_date, b.id or 0)):
            if budget.start_date <= today:
                latest[(budget.period, budget.category_id)] = budget
        categories = (await session.exec(select(Category).where(Category.user_id == user_id))).all()
        names = {item.id: item.name for item in categories}
        periods = {}
        for budget in latest.values():
            if (not budget.alert_enabled or budget.amount <= 0
                    or (budget.end_date and budget.end_date < today)
                    or budget.period not in {"daily", "weekly", "monthly", "quarterly", "yearly"}):
                continue
            if budget.period not in periods:
                start, end = ledger.period_bounds(budget.period, reference=local)
                amounts = await ledger.aggregate_by_category(
                    session, user_id=user_id, start=start.astimezone(timezone.utc),
                    end=end.astimezone(timezone.utc), direction=TransactionDirection.EXPENSE,
                )
                periods[budget.period] = (start.date().isoformat(), {item["category_id"]: item["amount"] for item in amounts})
            period_start, amounts = periods[budget.period]
            actual = sum(amounts.values()) if budget.category_id is None else amounts.get(budget.category_id, 0)
            ratio = actual / budget.amount
            level = 2 if ratio >= config.budget.over_ratio else 1 if ratio >= config.budget.warn_ratio else 0
            if not level:
                continue
            name = "总预算" if budget.category_id is None else names.get(budget.category_id, "未知类别")
            label = {"daily": "日", "weekly": "周", "monthly": "月", "quarterly": "季", "yearly": "年"}[budget.period]
            heading = "🔴 预算超支" if level == 2 else "🟡 预算预警"
            extra = f"，超出 {max(0, actual - budget.amount):.2f} 元" if level == 2 else "，接近预算上限"
            alerts.append(Alert(
                f"budget:{budget.period}:{period_start}:{budget.category_id}", level,
                f"{heading}：本{label}「{name}」已用 {actual:.2f} / {budget.amount:.2f} 元（{ratio * 100:.1f}%）{extra}。",
            ))

    if config.alert.low_balance_threshold > 0:
        accounts = await ledger.get_account_balances(session, user_id=user_id)
        for account in accounts:
            if account.get("balance", 0) < config.alert.low_balance_threshold:
                alerts.append(Alert(f"low_balance:{account['id']}", 1,
                    f"⚠️ 账户「{account['name']}」余额 {account['balance']:.2f} 元，低于安全线 {config.alert.low_balance_threshold:.2f} 元。"))
    return alerts
