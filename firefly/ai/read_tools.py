"""Read-only query contract. No SQL, filesystem, config or write tools are exposed.

Identity always comes from the caller; model-supplied user/account IDs are ignored.
The executor in assistant.py implements exactly these allowlisted operations.
"""
INTENTS = {
    "sum_by_category": "查询某个类别在某个周期的支出或收入合计",
    "sum_total": "查询某个周期的总收入、总支出或结余",
    "top_categories": "查询某个周期支出最高的几个类别",
    "count_transactions": "查询某个周期的记账笔数",
    "list_transactions": "列出某个周期或某个类别的流水明细",
    "compare_period": "对比两个周期的收支变化",
    "budget_status": "查询预算执行情况",
    "tax_status": "查询税务风险自检结论",
    "account_balance": "查询账本余额和净资产",
    "financial_overview": "读取收支、分类、预算与最近流水用于综合分析",
    "unknown": "无法判断用户意图",
}


def validate_plan(raw):
    if not isinstance(raw, dict):
        raw = {}
    intent = raw.get("intent")
    period = raw.get("period")
    def bounded(value, default, upper):
        try:
            return max(1, min(upper, int(value)))
        except (ValueError, TypeError, OverflowError):
            return default
    return {
        "intent": intent if isinstance(intent, str) and intent in INTENTS else "unknown",
        "period": period if isinstance(period, str) and period in {"daily", "weekly", "monthly", "quarterly", "yearly", "custom"} else "monthly",
        "direction": raw.get("direction") if raw.get("direction") in ("expense", "income", "all") else "expense",
        "category": str(raw.get("category") or "")[:64] or None,
        "top_n": bounded(raw.get("top_n"), 5, 10),
        "custom_days": bounded(raw.get("custom_days"), 30, 366),
    }
