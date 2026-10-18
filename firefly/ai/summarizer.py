"""AI 报告总结：把结构化 payload 交给大模型，产出「说人话」的解读。

为什么要单独一层，而不是直接让模板拼字符串：
    规则拼装能保证数字准确，但说不出「这个月为什么难看」。
    大模型擅长后者，不擅长前者 —— 所以这里的定位是**在既定事实之上做解释**，
    所有数字都由 payload 提供，提示词里明确要求「不得编造未提供的数字」。

降级策略：
    LLM 未配置 / 调用失败 / 返回内容不可用时，返回 ``fallback`` 文本而不是抛异常。
    报告推送是定时任务，绝不能因为一次 API 抖动就整条消息发不出去。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from loguru import logger

from .llm import LLMNotReadyError, chat, is_ready, safe_error

# 提示词单独放常量，便于论文里对照与迭代
SYSTEM_PROMPT = """你是一位资深的企业财税顾问，擅长把枯燥的财务数据翻译成老板能听懂的话。

要求：
1. 严格基于用户提供的 JSON 数据，**不得编造任何未提供的数字**。
2. 不要复述原始数字，而要说清「这意味着什么」和「接下来该做什么」。
3. 语言简洁、专业、口语化，避免「本报告显示」这类套话。
4. 如果数据里存在风险信号（异常支出、超预算、税负偏离、个人消费混入），
   要明确指出并给出优先级排序。
5. 只输出总结正文，不要 Markdown 标题、不要代码块围栏。"""


def _build_user_prompt(payload: Dict[str, Any]) -> str:
    """把 payload 压缩成模型看得懂的精简 JSON。

    不直接塞整个 payload：里面有为前端渲染准备的图表结构
    （waterfall、daily_expense 等），既占 token 又对总结没帮助，
    还会稀释模型对关键指标的注意力。
    """
    overview = payload.get("overview") or {}
    meta = payload.get("meta") or {}
    structure = payload.get("structure") or {}
    anomaly = payload.get("anomaly") or {}
    budget = payload.get("budget") or {}
    tax = payload.get("tax") or {}
    attribution = payload.get("attribution") or {}
    optimization = payload.get("optimization") or {}

    digest: Dict[str, Any] = {
        "期间": meta.get("period_label"),
        "统计区间": f"{str(meta.get('period_start'))[:10]} ~ {str(meta.get('period_end'))[:10]}",
        "流水笔数": overview.get("transaction_count"),
        "收入": overview.get("income"),
        "支出": overview.get("expense"),
        "结余": overview.get("net"),
        "账户余额": overview.get("balance"),
        "结余率(%)": overview.get("savings_rate"),
        "日均支出": overview.get("avg_daily_expense"),
    }

    compare = overview.get("compare") or {}
    digest["环比"] = {
        name: {
            "变化率(%)": (compare.get(key) or {}).get("ratio"),
            "方向": (compare.get(key) or {}).get("direction"),
        }
        for key, name in (("income", "收入"), ("expense", "支出"), ("net", "结余"))
        if (compare.get(key) or {}).get("ratio") is not None
    }

    categories = structure.get("expense_categories") or []
    if categories:
        digest["支出结构TOP"] = [
            {
                "类别": c.get("name"),
                "金额": c.get("amount"),
                "占比(%)": c.get("ratio"),
                "笔数": c.get("count"),
            }
            for c in categories[:6]
        ]

    counterparts = structure.get("top_counterparties") or []
    if counterparts:
        digest["主要往来对象"] = [
            {"对象": p.get("counterparty"), "金额": p.get("amount"), "笔数": p.get("count")}
            for p in counterparts[:4]
        ]

    if anomaly.get("anomaly_count") or anomaly.get("personal_mixed_count"):
        digest["异常"] = {
            "金额异常笔数": anomaly.get("anomaly_count"),
            "金额异常总额": anomaly.get("anomaly_amount"),
            "异常金额占比(%)": anomaly.get("anomaly_amount_ratio"),
            "个人消费混入笔数": anomaly.get("personal_mixed_count"),
        }

    if budget.get("has_budget"):
        digest["预算"] = {
            "总预算": budget.get("total_budget"),
            "已用": budget.get("total_actual"),
            "使用率(%)": budget.get("total_usage_ratio"),
            "超支类别": [
                {"类别": i.get("name"), "已用": i.get("actual"), "预算": i.get("budget")}
                for i in (budget.get("over_items") or [])[:5]
            ],
            "接近超支": [
                {"类别": i.get("name"), "使用率(%)": i.get("usage_ratio")}
                for i in (budget.get("warn_items") or [])[:5]
            ],
        }

    if tax.get("risk_count"):
        digest["税务自检"] = {
            "最高风险等级": tax.get("highest_level"),
            "风险项数": tax.get("risk_count"),
            "可税前扣除支出": tax.get("deductible_expense"),
            "发现": [
                {
                    "项目": c.get("title"),
                    "等级": c.get("level"),
                    "说明": c.get("description"),
                    "建议": c.get("suggestion"),
                }
                for c in (tax.get("checks") or [])
                if c.get("level") not in (None, "info")
            ][:6],
        }

    if attribution.get("summary"):
        digest["环比归因"] = attribution.get("summary")

    actions = optimization.get("action_items") or []
    if actions:
        digest["系统给出的行动建议"] = [
            {"优先级": a.get("priority"), "标题": a.get("title"), "说明": a.get("detail")}
            for a in actions[:8]
        ]

    return (
        "以下是一份企业财务税务报告的原始数据（JSON）：\n\n"
        f"{json.dumps(digest, ensure_ascii=False, default=str)}\n\n"
        "请写一段 200-400 字的中文总结，覆盖："
        "① 本期整体经营与现金状况的定性判断；"
        "② 最值得警惕的 1-3 个问题及其原因；"
        "③ 接下来最该做的 2-3 件事（按优先级）。"
    )


async def summarize_report(
    payload: Dict[str, Any],
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    fallback: str = "",
) -> Dict[str, Any]:
    """用大模型为报告生成一段自然语言总结。

    :param temperature: 调用温度。为 ``None`` 时用 config 的默认值。
        这个参数暴露给前端是有意为之 —— 温度高时措辞更灵活、
        温度低时更稳定克制，用户可以在设置里自行权衡。
    :param max_tokens: 输出长度上限。为 ``None`` 时用 config 的默认值。
    :param fallback: LLM 不可用时返回的文本（通常是规则拼装的摘要）
    :return: ``{text, engine, temperature, max_tokens, elapsed_ms, error}``

    任何异常都被吞掉并写进 ``error``：报告推送是定时任务，
    不能因为一次 API 抖动就整条消息发不出去。
    """
    import time

    started = time.perf_counter()

    result: Dict[str, Any] = {
        "text": fallback,
        "engine": "fallback",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "elapsed_ms": 0,
        "error": None,
    }

    if not is_ready():
        result["error"] = "LLM 未就绪，请检查 llm.enabled、llm.api_key 或环境变量"
        return result

    if not payload:
        result["error"] = "报告数据为空，无法总结"
        return result

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(payload)},
        ]

        # 总结属于创作型任务，默认温度比抽取/分类高一些（那边是 0.0）
        text = await chat(
            messages,
            temperature=0.5 if temperature is None else temperature,
            max_tokens=max_tokens,
            use_cache=False,  # 同一份 payload 每次都该重新组织语言
        )

        text = (text or "").strip()
        if not text:
            result["error"] = "模型返回了空内容"
            return result

        result["text"] = text
        result["engine"] = "llm"
    except LLMNotReadyError as exc:
        result["error"] = f"LLM 未就绪：{exc}"
    except Exception as exc:  # noqa: BLE001 - 总结失败不应影响推送
        logger.warning("AI 总结失败：{}", type(exc).__name__)
        result["error"] = safe_error(exc)
    finally:
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)

    return result


__all__ = ["summarize_report", "SYSTEM_PROMPT"]
