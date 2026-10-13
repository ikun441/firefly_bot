"""对话式查询助手。

设计立场：**不做裸 NL2SQL**。
理由有三 ——
1. 安全性：让模型自由生成 SQL 意味着它可能读到不该读的表、写出 UPDATE/DELETE；
2. 可控性：报表口径需要稳定复现，自由 SQL 每次结果可能不同，无法写进论文实验；
3. 可解释性：答辩时被问「这个数怎么来的」，函数调用能明确指到某个聚合函数。

因此采用「意图识别 + 参数抽取 + 白名单函数执行」三段式：
LLM 只负责回答"用户在问什么"（分类 + 抽参数），真正的计算交给 ``ledger`` 服务层。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..services import ledger as ledger_service
from .llm import LLMNotReadyError, chat_json, is_ready

# ------------------------------------------------------------------------------
# 白名单意图
# ------------------------------------------------------------------------------

INTENTS = {
    "sum_by_category": "查询某个类别在某个周期的支出或收入合计",
    "sum_total": "查询某个周期的总收入、总支出或结余",
    "top_categories": "查询某个周期支出最高的几个类别",
    "count_transactions": "查询某个周期的记账笔数",
    "list_transactions": "列出某个周期或某个类别的流水明细",
    "compare_period": "对比两个周期的收支变化",
    "budget_status": "查询预算执行情况",
    "tax_status": "查询税务风险自检结论",
    "unknown": "无法判断用户意图",
}

PLANNER_PROMPT = """你是一个财税记账助手的意图解析器。用户会用中文提问，请判断其意图并抽取参数。

可选意图：
- sum_by_category: 查询某类别的合计（如「上个月餐饮花了多少」）
- sum_total: 查询总收入/总支出/结余（如「这个月花了多少」「结余多少」）
- top_categories: 支出最高的类别排行（如「哪些类目花得最多」）
- count_transactions: 记账笔数
- list_transactions: 列出明细
- compare_period: 对比两个周期（如「这个月比上个月多花了吗」）
- budget_status: 预算执行情况
- tax_status: 税务风险自检
- unknown: 无法判断

周期字段 period 只能取：daily / weekly / monthly / quarterly / yearly / custom
方向字段 direction 只能取：expense / income / all
数字字段 top_n 为整数（默认 5）；custom_days 为自定义周期天数。

只输出 JSON，不要解释、不要 markdown。格式：
{"intent": "sum_by_category", "category": "餐饮", "period": "monthly", "direction": "expense", "top_n": 5, "custom_days": null}"""


# ------------------------------------------------------------------------------
# 规则兜底：无 LLM 时的意图识别
# ------------------------------------------------------------------------------

PERIOD_KEYWORDS = [
    (["今天", "今日", "当天"], "daily"),
    (["这周", "本周", "上周", "一周"], "weekly"),
    (["去年", "年度", "整年", "一年"], "yearly"),
    (["本季", "这个季度", "季度"], "quarterly"),
    (["这个月", "本月", "上个月", "上月", "当月", "月度", "每月"], "monthly"),
]


def rule_plan(question: str) -> Dict[str, Any]:
    """无 LLM 时的意图识别：关键词匹配 + 类别名提取。"""
    plan: Dict[str, Any] = {"intent": "unknown", "period": "monthly", "direction": "expense", "top_n": 5}

    for keywords, period in PERIOD_KEYWORDS:
        if any(keyword in question for keyword in keywords):
            plan["period"] = period
            break

    # 方向
    if re.search(r"(收入|赚|进账|收款)", question):
        plan["direction"] = "income"
    elif re.search(r"(结余|净|剩|剩下)", question):
        plan["direction"] = "all"

    # 对比
    if re.search(r"(比上|对比|环比|多花|少花|变化)", question):
        plan["intent"] = "compare_period"
        return plan

    # 预算
    if "预算" in question:
        plan["intent"] = "budget_status"
        return plan

    # 税务
    if re.search(r"(税|风险|申报|发票)", question):
        plan["intent"] = "tax_status"
        return plan

    # 排行
    if re.search(r"(最多|排行|top|前几|哪些)", question):
        plan["intent"] = "top_categories"
        match = re.search(r"(\d+)\s*(?:个|项|名|类)", question)
        if match:
            plan["top_n"] = int(match.group(1))
        return plan

    # 笔数
    if re.search(r"(几笔|多少笔|次数|条数)", question):
        plan["intent"] = "count_transactions"
        return plan

    # 明细
    if re.search(r"(明细|列出|都有哪些|详情|记录)", question):
        plan["intent"] = "list_transactions"
        return plan

    # 类别合计：尝试匹配类别名
    from .natural import CATEGORY_KEYWORDS

    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in question or any(keyword in question for keyword in keywords):
            plan["intent"] = "sum_by_category"
            plan["category"] = category
            return plan

    # 总额
    if re.search(r"(多少|合计|一共|总共|花销|开销|支出|收入)", question):
        plan["intent"] = "sum_total"
        return plan

    return plan


# ------------------------------------------------------------------------------
# 参数解析与执行
# ------------------------------------------------------------------------------


def _resolve_period(plan: Dict[str, Any], question: str) -> tuple[str, Optional[int]]:
    """把 plan 里的周期描述解析成 period_bounds 能接受的参数。"""
    period = plan.get("period") or "monthly"
    if period not in ("daily", "weekly", "monthly", "quarterly", "yearly", "custom"):
        period = "monthly"

    custom_days = plan.get("custom_days")
    try:
        custom_days = int(custom_days) if custom_days else None
    except (TypeError, ValueError):
        custom_days = None

    # 上周 / 上月 需要把参考时间往前挪一个周期
    reference = None
    if re.search(r"(上个月|上月|上个季度|去年|上周)", question) and period != "daily":
        spans = {"weekly": timedelta(days=7), "monthly": timedelta(days=30), "quarterly": timedelta(days=91), "yearly": timedelta(days=365)}
        reference = datetime.now() - spans.get(period, timedelta(days=30))

    return period, custom_days, reference


async def _execute(session: AsyncSession, *, user_id: str, plan: Dict[str, Any], question: str) -> Dict[str, Any]:
    """执行白名单函数并把结果整理成前端可直接渲染的结构。"""
    intent = plan.get("intent", "unknown")
    period, custom_days, reference = _resolve_period(plan, question)

    start, end = ledger_service.period_bounds(period, reference=reference, custom_days=custom_days)
    label = ledger_service.format_period_label(period, start, end)

    if intent == "sum_by_category":
        category_name = plan.get("category")
        categories = await ledger_service.list_categories(session, user_id=user_id)
        matched = None
        for category in categories:
            if category_name and category.name == category_name:
                matched = category
                break
        # 模糊匹配兜底
        if matched is None and category_name:
            for category in categories:
                if category_name in category.name or category.name in category_name:
                    matched = category
                    break

        if matched is None:
            # 没有匹配的类别，退化为结构分析
            rows = await ledger_service.aggregate_by_category(
                session, user_id=user_id, start=start, end=end, direction="expense", top_n=10
            )
            if rows:
                top = rows[0]
                answer = f"{label}共支出 {top['amount']:.2f} 元，其中「{top['name']}」最多，为 {top['amount']:.2f} 元。"
                return {
                    "answer": answer,
                    "intent": intent,
                    "data": {"period_label": label, "categories": rows},
                    "chart": {"type": "bar", "data": rows, "x": "name", "y": "amount"},
                }
            return {"answer": f"{label}还没有记账记录。", "intent": intent, "data": {}}

        rows = await ledger_service.aggregate_by_category(
            session, user_id=user_id, start=start, end=end, direction="expense", top_n=50
        )
        target = next((row for row in rows if row["category_id"] == matched.id), None)
        amount = target["amount"] if target else 0.0
        count = target["count"] if target else 0
        ratio = target["ratio"] if target else 0.0

        total = sum(row["amount"] for row in rows) or 1.0
        answer = (
            f"{label}「{matched.name}」共支出 {amount:.2f} 元（{count} 笔），"
            f"占总支出的 {amount / total * 100:.1f}%。"
        )
        return {
            "answer": answer,
            "intent": intent,
            "data": {"category": matched.name, "amount": amount, "count": count, "ratio": ratio, "period_label": label},
            "chart": {"type": "pie", "data": [{"name": row["name"], "value": row["amount"]} for row in rows[:6]]},
        }

    if intent == "sum_total":
        summary = await ledger_service.sum_by_direction(session, user_id=user_id, start=start, end=end)
        if plan.get("direction") == "income":
            answer = f"{label}收入 {summary['income']:.2f} 元。"
        elif plan.get("direction") == "all":
            answer = (
                f"{label}收入 {summary['income']:.2f} 元，支出 {summary['expense']:.2f} 元，"
                f"结余 {summary['net']:.2f} 元。"
            )
        else:
            answer = f"{label}共支出 {summary['expense']:.2f} 元。"
        return {
            "answer": answer,
            "intent": intent,
            "data": {"period_label": label, **summary},
            "chart": {"type": "bar", "data": [
                {"name": "收入", "value": summary["income"]},
                {"name": "支出", "value": summary["expense"]},
                {"name": "结余", "value": summary["net"]},
            ], "x": "name", "y": "value"},
        }

    if intent == "top_categories":
        top_n = plan.get("top_n") or 5
        try:
            top_n = max(1, min(10, int(top_n)))
        except (TypeError, ValueError):
            top_n = 5

        rows = await ledger_service.aggregate_by_category(
            session, user_id=user_id, start=start, end=end, direction="expense", top_n=top_n
        )
        if not rows:
            return {"answer": f"{label}还没有支出记录。", "intent": intent, "data": {}}

        lines = [f"{index + 1}. {row['name']} {row['amount']:.2f} 元" for index, row in enumerate(rows)]
        answer = f"{label}支出最高的 {len(rows)} 个类别：\n" + "\n".join(lines)
        return {
            "answer": answer,
            "intent": intent,
            "data": {"period_label": label, "categories": rows},
            "chart": {"type": "bar", "data": rows, "x": "name", "y": "amount"},
        }

    if intent == "count_transactions":
        total = await ledger_service.count_transactions(
            session, user_id=user_id, start=start, end=end
        )
        return {
            "answer": f"{label}共记录 {total} 笔流水。",
            "intent": intent,
            "data": {"period_label": label, "count": total},
        }

    if intent == "list_transactions":
        rows = await ledger_service.list_transactions(
            session, user_id=user_id, start=start, end=end, limit=10
        )
        if not rows:
            return {"answer": f"{label}没有找到流水记录。", "intent": intent, "data": {}}

        lines = [
            f"· {row.trade_time.strftime('%m-%d')} {row.memo or '未备注'} {row.amount:.2f} 元"
            for row in rows
        ]
        return {
            "answer": f"{label}最近的流水：\n" + "\n".join(lines),
            "intent": intent,
            "data": {
                "period_label": label,
                "items": [
                    {
                        "id": row.id,
                        "amount": row.amount,
                        "direction": row.direction.value if hasattr(row.direction, "value") else str(row.direction),
                        "memo": row.memo,
                        "trade_time": row.trade_time.isoformat(),
                    }
                    for row in rows
                ],
            },
        }

    if intent == "compare_period":
        current = await ledger_service.sum_by_direction(session, user_id=user_id, start=start, end=end)
        prev_start, prev_end = ledger_service.previous_period_bounds(period, start, end)
        previous = await ledger_service.sum_by_direction(
            session, user_id=user_id, start=prev_start, end=prev_end
        )

        delta = current["expense"] - previous["expense"]
        ratio = (delta / previous["expense"] * 100) if previous["expense"] else None
        trend_word = "增加了" if delta > 0 else "减少了" if delta < 0 else "持平"

        answer = (
            f"{label}支出 {current['expense']:.2f} 元，上一周期 {previous['expense']:.2f} 元，"
            f"{trend_word} {abs(delta):.2f} 元"
        )
        answer += f"（{abs(ratio):.1f}%）。" if ratio is not None else "。"

        return {
            "answer": answer,
            "intent": intent,
            "data": {
                "period_label": label,
                "current": current,
                "previous": previous,
                "delta": delta,
                "ratio": ratio,
            },
            "chart": {
                "type": "bar",
                "data": [
                    {"name": "上期", "value": previous["expense"]},
                    {"name": "本期", "value": current["expense"]},
                ],
                "x": "name",
                "y": "value",
            },
        }

    if intent == "budget_status":
        from ..services import budget as budget_service

        status = await budget_service.get_budget_status(
            session, user_id=user_id, start=start, end=end, period=period
        )
        if not status.get("has_budget"):
            return {
                "answer": "你还没有设置预算，可以在「预算」页为常用类别设定额度。",
                "intent": intent,
                "data": status,
            }

        answer = (
            f"{status['period_label']}预算合计 {status['total_budget']:.2f} 元，"
            f"已使用 {status['total_actual']:.2f} 元（{status['total_usage_ratio'] * 100:.1f}%），"
            f"剩余 {status['total_remaining']:.2f} 元。"
        )
        if status.get("over_items"):
            names = "、".join(item["name"] for item in status["over_items"][:3])
            answer += f"\n已超支类别：{names}。"
        return {"answer": answer, "intent": intent, "data": status}

    if intent == "tax_status":
        from ..services import taxcheck as taxcheck_service

        result = await taxcheck_service.run_tax_check(
            session, user_id=user_id, start=start, end=end, persist=False, create_suggestions=False
        )
        counts = result["counts"]
        answer = (
            f"{label}税务自检完成：高风险 {counts.get('high', 0)} 项、"
            f"中风险 {counts.get('medium', 0)} 项、低风险 {counts.get('low', 0)} 项。"
        )
        if result.get("optimizations"):
            answer += "\n首要优化方向：" + result["optimizations"][0]["title"]
        return {"answer": answer, "intent": intent, "data": result}

    return {
        "answer": (
            "抱歉，我还没理解这个问题。你可以试着这样问：\n"
            "· 上个月餐饮花了多少？\n"
            "· 这个月支出最高的类别是哪些？\n"
            "· 这个月比上个月多花了吗？\n"
            "· 我的预算执行得怎么样？"
        ),
        "intent": "unknown",
        "data": {},
    }


# ------------------------------------------------------------------------------
# 对外入口
# ------------------------------------------------------------------------------


async def answer_question(
    session: AsyncSession,
    *,
    user_id: str,
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """回答用户问题：LLM 规划 → 白名单执行；LLM 不可用时用规则规划。"""
    config = get_config()

    plan: Optional[Dict[str, Any]] = None
    engine = "rule"

    if config.llm.enabled and is_ready():
        now = datetime.now()
        context = (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"用户问题：{question}"
        )
        try:
            plan = await chat_json(
                [
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.0,
            )
            engine = "llm"
        except LLMNotReadyError:
            plan = None
        except Exception:  # noqa: BLE001 - 规划失败退回规则，绝不中断对话
            plan = None

    if not plan or not plan.get("intent"):
        plan = rule_plan(question)
        engine = "rule"

    # 规则抽取的类别名可能和数据库里的不完全一致，这里再兜一层
    result = await _execute(session, user_id=user_id, plan=plan, question=question)
    result["engine"] = engine
    result["plan"] = plan
    result["question"] = question
    return result


__all__ = ["answer_question", "rule_plan", "INTENTS"]
