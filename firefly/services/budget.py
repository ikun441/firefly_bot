"""流萤 Firefly · 预算服务

预算 vs 实际 —— 财务人最看重的功能，且纯计算、无 AI 依赖、零风险。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_config
from ..db.models import Budget, Category, Transaction, TransactionDirection
from .ledger import aggregate_by_category

# 报告/前端展示的周期选项
PERIOD_LABELS: Dict[str, str] = {
    "daily": "按日",
    "weekly": "按周",
    "monthly": "按月",
    "quarterly": "按季",
    "yearly": "按年",
}


async def create_budget(
    session: AsyncSession,
    *,
    user_id: int,
    amount: float,
    category_id: Optional[int] = None,
    period: str = "monthly",
    start_date: Optional[date] = None,
    alert_enabled: bool = True,
) -> Budget:
    """创建预算（同一范围内重复创建会先移除旧记录）"""
    start = start_date or date.today()

    stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.category_id == category_id,
        Budget.period == period,
        Budget.start_date == start,
    )
    if category_id is None:
        stmt = stmt.where(Budget.category_id == None)  # noqa: E711
    result = await session.exec(stmt)
    existing = result.first()
    if existing is not None:
        await session.delete(existing)
        await session.flush()

    budget = Budget(
        user_id=user_id,
        category_id=category_id,
        amount=abs(float(amount)),
        period=period,
        start_date=start,
        alert_enabled=alert_enabled,
    )
    session.add(budget)
    await session.flush()
    return budget


async def list_budgets(session: AsyncSession, *, user_id: int) -> List[Budget]:
    """列出全部预算"""
    stmt = select(Budget).where(Budget.user_id == user_id).order_by(Budget.period)
    result = await session.exec(stmt)
    return list(result.all())


async def delete_budget(session: AsyncSession, budget_id: int, user_id: int) -> bool:
    """删除预算"""
    stmt = select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    result = await session.exec(stmt)
    budget = result.first()
    if budget is None:
        return False
    await session.delete(budget)
    return True


async def get_budget_status(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    period: str = "monthly",
) -> Dict[str, Any]:
    """计算预算执行情况

    Returns:
        含总预算、类别预算、使用率与超支清单的结构
    """
    config = get_config()

    # 实际支出（按类别）
    actual_by_category = await aggregate_by_category(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.EXPENSE,
    )
    actual_map = {item["category_id"]: item for item in actual_by_category}
    total_actual = sum(item["amount"] for item in actual_by_category)

    # 预算配置
    stmt = select(Budget).where(Budget.user_id == user_id, Budget.period == period)
    result = await session.exec(stmt)
    budgets = list(result.all())

    # 类别名称映射
    cat_stmt = select(Category)
    cat_result = await session.exec(cat_stmt)
    categories = {c.id: c for c in cat_result.all()}

    items: List[Dict[str, Any]] = []
    total_budget = 0.0

    for budget in budgets:
        if budget.category_id is None:
            total_budget += budget.amount
            continue

        category = categories.get(budget.category_id)
        actual = float(actual_map.get(budget.category_id, {}).get("amount", 0.0))
        usage = actual / budget.amount if budget.amount else 0.0

        if usage >= config.budget.over_ratio:
            state = "over"
        elif usage >= config.budget.warn_ratio:
            state = "warn"
        else:
            state = "ok"

        items.append(
            {
                "budget_id": budget.id,
                "category_id": budget.category_id,
                "name": category.name if category else "未知类别",
                "icon": category.icon if category else "📦",
                "color": category.color if category else "#94a3b8",
                "budget": round(budget.amount, 2),
                "actual": round(actual, 2),
                "remaining": round(budget.amount - actual, 2),
                "usage_ratio": round(usage * 100, 2),
                "state": state,
            }
        )

    items.sort(key=lambda x: x["usage_ratio"], reverse=True)

    # 未设预算但有支出的类别也纳入展示，避免"看不见的开销"
    budgeted_ids = {item["category_id"] for item in items}
    for item in actual_by_category:
        if item["category_id"] in budgeted_ids:
            continue
        items.append(
            {
                "budget_id": None,
                "category_id": item["category_id"],
                "name": item["name"],
                "icon": item["icon"],
                "color": item["color"],
                "budget": 0.0,
                "actual": item["amount"],
                "remaining": round(-item["amount"], 2),
                "usage_ratio": None,
                "state": "unbudgeted",
            }
        )

    # 总预算：显式配置优先，否则用类别预算之和
    explicit_total = total_budget
    derived_total = sum(item["budget"] for item in items if item["budget"] > 0)
    effective_total = explicit_total if explicit_total > 0 else derived_total

    total_usage = total_actual / effective_total if effective_total else 0.0
    if effective_total == 0:
        overall_state = "unbudgeted"
    elif total_usage >= config.budget.over_ratio:
        overall_state = "over"
    elif total_usage >= config.budget.warn_ratio:
        overall_state = "warn"
    else:
        overall_state = "ok"

    over_items = [item for item in items if item["state"] == "over"]
    warn_items = [item for item in items if item["state"] == "warn"]

    return {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "total_budget": round(effective_total, 2),
        "total_actual": round(total_actual, 2),
        "total_remaining": round(effective_total - total_actual, 2),
        "total_usage_ratio": round(total_usage * 100, 2),
        "overall_state": overall_state,
        "items": items,
        "over_count": len(over_items),
        "warn_count": len(warn_items),
        "over_items": over_items,
        "warn_items": warn_items,
        "has_budget": effective_total > 0,
    }


__all__ = [
    "PERIOD_LABELS",
    "create_budget",
    "list_budgets",
    "delete_budget",
    "get_budget_status",
]
