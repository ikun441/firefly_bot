"""智能分类与类别自演化。

两个能力共用一套「LLM 冷启动 + 用户纠正 few-shot」的机制：

* ``classify_memo``   —— 给一笔备注推荐类别（越用越准的可做实验点）
* ``suggest_tax_account`` —— 用户新建类别时，建议它该挂到哪个税务口径科目

关键设计：few-shot 示例**从用户自己的历史纠正里取**，而不是写死提示词。
这让「准确率随使用次数提升」成为可量化结论，而不是宣传话术。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db.models import Category, Transaction
from .llm import LLMNotReadyError, chat_json, is_ready

# 与 ledger.ensure_default_categories 中的 tax_account 保持一致
TAX_ACCOUNTS = [
    "业务招待费",
    "差旅费",
    "办公费",
    "租赁费",
    "水电费",
    "通信费",
    "存货采购",
    "广告宣传费",
    "职工教育经费",
    "职工福利费",
    "工资薪金",
    "主营业务收入",
    "其他业务收入",
    "不可扣除",
]

CLASSIFY_PROMPT = """你是一个记账分类助手。请根据备注内容，判断它属于哪个支出类别。

要求：
1. 只输出 JSON，不要解释、不要 markdown 代码块。
2. category 必须从给定类别列表中选择，不要发明新类别。
3. confidence 为你对该判断的置信度（0 到 1）。
4. reason 用一句中文说明判断依据，不超过 30 字。

输出格式：{"category": "餐饮", "confidence": 0.92, "reason": "出现「午餐」关键词"}"""

TAX_ACCOUNT_PROMPT = """你是一个税务科目映射助手。用户要新建一个记账类别，请判断它应该挂到哪个税务口径科目。

可选税务科目：""" + "、".join(TAX_ACCOUNTS) + """

要求：
1. 只输出 JSON，不要解释、不要 markdown 代码块。
2. tax_account 只能从上面的列表中选一个；如果确实无法对应，选「不可扣除」。
3. deductible 表示该类别支出能否在企业所得税前扣除（true / false）。
4. confidence 为置信度（0 到 1）。
5. reason 用一句中文说明依据，不超过 40 字。

输出格式：{"tax_account": "业务招待费", "deductible": true, "confidence": 0.88, "reason": "客户接待相关支出"}"""


async def _get_few_shot_examples(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 8,
) -> List[Dict[str, str]]:
    """从用户历史流水中取「高置信度」样本作为 few-shot 示例。

    只取已被确认且带类别的记录 —— 未经确认的数据本身就是噪声，
    喂给模型只会让分类漂移。
    """
    from sqlmodel import select

    statement = (
        select(Transaction, Category)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.category_id.is_not(None),  # type: ignore[union-attr]
            Transaction.memo.is_not(None),  # type: ignore[union-attr]
            Transaction.is_confirmed == True,  # noqa: E712
        )
        .order_by(Transaction.trade_time.desc())  # type: ignore[attr-defined]
        .limit(limit * 3)
    )

    rows = (await session.execute(statement)).all()

    examples: List[Dict[str, str]] = []
    seen: set[tuple] = set()

    for txn, category in rows:
        memo = (txn.memo or "").strip()
        if not memo or len(memo) > 40:
            continue
        key = (memo[:12], category.name)
        if key in seen:
            continue
        seen.add(key)
        examples.append({"memo": memo, "category": category.name})
        if len(examples) >= limit:
            break

    return examples


def _rule_classify(memo: str, categories: List[str]) -> Optional[Dict[str, Any]]:
    """规则兜底分类：复用自然语言抽取的关键词表。"""
    from .natural import CATEGORY_KEYWORDS

    for category, keywords in CATEGORY_KEYWORDS.items():
        if category not in categories:
            continue
        hits = [keyword for keyword in keywords if keyword in memo]
        if hits:
            return {
                "category": category,
                "confidence": min(0.6 + 0.1 * len(hits), 0.85),
                "reason": f"命中关键词「{hits[0]}」",
                "engine": "rule",
            }
    return None


async def classify_memo(
    session: AsyncSession,
    *,
    user_id: str,
    memo: str,
    categories: Optional[List[Category]] = None,
) -> Dict[str, Any]:
    """给一笔备注推荐类别。

    Returns:
        ``{"suggestion": {...} | None, "candidates": [...], "engine": str}``
    """
    from ..services import ledger as ledger_service

    if categories is None:
        categories = await ledger_service.list_categories(session, user_id=user_id)

    names = [category.name for category in categories if category.direction.value == "expense"]
    if not names:
        return {"success": False, "suggestion": None, "candidates": [], "engine": "none", "message": "没有可用的支出类别"}

    config = get_config()
    engine = "rule"
    suggestion: Optional[Dict[str, Any]] = None

    if config.llm.enabled and is_ready():
        examples = await _get_few_shot_examples(session, user_id=user_id)
        example_text = ""
        if examples:
            example_text = "\n\n用户历史记账习惯（请参考这些偏好）：\n" + "\n".join(
                f"- 「{example['memo']}」→ {example['category']}" for example in examples
            )

        prompt = (
            f"可选类别：{'、'.join(names)}\n"
            f"待分类备注：{memo}"
            f"{example_text}"
        )

        try:
            result = await chat_json(
                [
                    {"role": "system", "content": CLASSIFY_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )

            category_name = result.get("category")
            if category_name in names:
                matched = next((c for c in categories if c.name == category_name), None)
                suggestion = {
                    "category_id": matched.id if matched else None,
                    "name": category_name,
                    "icon": matched.icon if matched else "🏷️",
                    "color": matched.color if matched else None,
                    "confidence": float(result.get("confidence", 0.85)),
                    "reason": result.get("reason", "AI 分类"),
                    "engine": "llm",
                }
                engine = "llm"
        except LLMNotReadyError:
            suggestion = None
        except Exception:  # noqa: BLE001 - 分类失败不应阻断记账流程
            suggestion = None

    if suggestion is None:
        suggestion = _rule_classify(memo, names)
        engine = suggestion.get("engine", "rule") if suggestion else "rule"
        if suggestion:
            matched = next((c for c in categories if c.name == suggestion["category"]), None)
            if matched:
                suggestion["category_id"] = matched.id
                suggestion["name"] = matched.name
                suggestion["icon"] = matched.icon

    # 候选列表（按关键词命中度排序），供前端展示「其他可能」
    candidates = []
    for category in categories:
        if category.direction.value != "expense":
            continue
        if suggestion and category.name == suggestion.get("name"):
            continue
        candidates.append({"id": category.id, "name": category.name, "icon": category.icon})

    return {
        "success": suggestion is not None,
        "suggestion": suggestion,
        "candidates": candidates[:8],
        "engine": engine,
        "memo": memo,
    }


async def suggest_tax_account(name: str) -> Dict[str, Any]:
    """类别自演化：为新类别推荐税务口径科目。"""
    config = get_config()

    if config.llm.enabled and is_ready():
        try:
            result = await chat_json(
                [
                    {"role": "system", "content": TAX_ACCOUNT_PROMPT},
                    {"role": "user", "content": f"用户要新建的类别名称：{name}"},
                ],
                temperature=0.0,
            )
            account = result.get("tax_account")
            if account in TAX_ACCOUNTS:
                return {
                    "success": True,
                    "name": name,
                    "tax_account": account,
                    "deductible": bool(result.get("deductible", True)),
                    "confidence": float(result.get("confidence", 0.8)),
                    "reason": result.get("reason", "AI 建议"),
                    "engine": "llm",
                    "candidates": TAX_ACCOUNTS,
                }
        except LLMNotReadyError:
            pass
        except Exception:  # noqa: BLE001
            pass

    # 规则兜底：关键词映射
    fallback_map = {
        "招待": ("业务招待费", True),
        "差旅": ("差旅费", True),
        "交通": ("差旅费", True),
        "办公": ("办公费", True),
        "房租": ("租赁费", True),
        "水电": ("水电费", True),
        "通信": ("通信费", True),
        "话费": ("通信费", True),
        "采购": ("存货采购", True),
        "进货": ("存货采购", True),
        "广告": ("广告宣传费", True),
        "推广": ("广告宣传费", True),
        "培训": ("职工教育经费", True),
        "学习": ("职工教育经费", True),
        "工资": ("工资薪金", True),
        "福利": ("职工福利费", True),
        "收入": ("主营业务收入", False),
    }

    for keyword, (account, deductible) in fallback_map.items():
        if keyword in name:
            return {
                "success": True,
                "name": name,
                "tax_account": account,
                "deductible": deductible,
                "confidence": 0.7,
                "reason": f"命中关键词「{keyword}」的常见税务口径",
                "engine": "rule",
                "candidates": TAX_ACCOUNTS,
            }

    return {
        "success": True,
        "name": name,
        "tax_account": "不可扣除",
        "deductible": False,
        "confidence": 0.5,
        "reason": "无法确定税务口径，建议咨询专业人士后手动调整",
        "engine": "rule",
        "candidates": TAX_ACCOUNTS,
    }


__all__ = ["classify_memo", "suggest_tax_account"]
