"""AI 能力接口：自然语言记账、对话式查询、智能分类。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SuggestionKind
from ..db.session import get_session
from ..services import audit as audit_service
from ..services import ledger as ledger_service
from .deps import MAX_AMOUNT, fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["ai"])


def _as_int(value: Union[int, str, None]) -> Optional[int]:
    """把可选的 id 归一化成 int；空串 / None / 非法值都返回 None。

    与 firefly/api/transaction.py、budget.py 的同名助手保持一致：
    前端 <select> 的 value 永远是字符串，不归一化会让 account_id 以
    "1" 的形式落库，与整数 1 是两个值，账户余额聚合时匹配不上。
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


class NaturalIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, description="自然语言描述，例如「昨天打车 38」")
    auto_create: bool = Field(default=False, description="是否直接入库（否则返回待确认卡片）")

class ChatIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    history: List[Dict[str, str]] = Field(default_factory=list, max_length=20)


class ClassifyIn(BaseModel):
    memo: str = Field(..., min_length=1)


class NLItemIn(BaseModel):
    """用户在前端核对过的一条自然语言条目。"""

    # 只卡上界，保留原来的 gt=0 语义由本文件 `if not item.amount or item.amount <= 0`
    # 处理 —— 那里是 **continue（跳过该条）**，不是报错。
    # 若写成 Field(gt=0)，一条负数会把整批 NL 确认变成 422 全失败，
    # 而用户本意只是其中一条有问题，其余本该正常落库。
    amount: float = Field(..., le=MAX_AMOUNT, allow_inf_nan=False)
    direction: str = Field(default="expense")
    category: Optional[str] = None
    # 同理用 Union[int, str]：前端在「识别出的类别名匹配不到已有类别」时
    # 会传空串 ''，只写 Optional[int] 会让 pydantic 直接 422，
    # 整批确认全部失败。归一化交给下面的 _as_int，空串→None（未分类）。
    category_id: Optional[Union[int, str]] = None
    # 自然语言只负责抽出金额/类别/备注，**账本归属得由用户在确认时选**。
    # 此前这个字段根本不存在，前端即使传了也会被 pydantic 直接丢弃，
    # 落库时 account_id 恒为 None —— 流水永远不进任何账户余额。
    # 用 Union[int, str] 与 transaction.py 保持一致：前端传的是 select 的
    # 字符串值，只收 int 会让所有账本选择静默失效。
    account_id: Optional[Union[int, str]] = None
    trade_time: Optional[str] = None
    counterparty: Optional[str] = None
    memo: Optional[str] = None
    confidence: Optional[float] = None
    has_invoice: bool = False


class NLConfirmIn(BaseModel):
    items: List[NLItemIn] = Field(default_factory=list, max_length=100)


@router.get("/ai/status")
async def ai_status() -> Dict[str, Any]:
    """LLM 可用性状态（前端据此决定是否展示 AI 入口）。"""
    from ..ai.llm import is_ready
    from ..config import get_config

    cfg = get_config()
    return ok(
        {
            "enabled": cfg.llm.enabled,
            "ready": is_ready(),
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "hint": "" if is_ready() else "请配置 llm.api_key 或环境变量 LLM_API_KEY 后重启",
        }
    )


@router.post("/ai/natural")
async def natural_language(
    payload: NaturalIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """自然语言记账：LLM 结构化抽取 → 待确认卡片。"""
    user = await get_or_create_user(session)

    from ..ai.natural import extract_transactions

    categories = await ledger_service.list_categories(session, user_id=user.id)
    category_names = [c.name for c in categories]

    result = await extract_transactions(payload.text, categories=category_names)

    if not result.get("success"):
        return fail(result.get("message") or "解析失败", detail=result)

    items = result.get("items", [])
    for item in items:
        item["category_id"] = next((c.id for c in categories if c.name == item.get("category") and c.direction == item.get("direction")), None)
    created: List[Dict[str, Any]] = []

    if payload.auto_create:
        for item in items:
            from ..db.models import TransactionDirection, TransactionSource

            direction = (
                TransactionDirection.EXPENSE
                if item.get("direction", "expense") == "expense"
                else TransactionDirection.INCOME
            )
            txn = await ledger_service.create_transaction(
                session,
                user_id=user.id,
                amount=float(item.get("amount", 0)),
                direction=direction,
                category_id=item.get("category_id"),
                trade_time=item.get("trade_time"),
                counterparty=item.get("counterparty"),
                memo=item.get("memo"),
                raw_input=payload.text,
                source=TransactionSource.NATURAL_LANGUAGE,
                confidence=item.get("confidence"),
                is_confirmed=True,
                has_invoice=item.get("has_invoice", False),
            )
            created.append(to_dict(txn))
            await audit_service.log_action(session, "create_transaction", user_id=user.id, target_type="transaction", target_id=txn.id, channel="web", detail="自然语言自动入账", extra={"after": to_dict(txn)})

    await audit_service.log_action(
        session,
        "ai_natural_record",
        user_id=user.id,
        target_type="ai",
        operator="ai",
        detail=f"自然语言记账抽取 {len(items)} 条",
        extra={"input": payload.text, "auto_create": payload.auto_create},
        channel="web",
    )

    return ok(
        {
            "items": items,
            "created": created,
            "count": len(items),
            "raw": payload.text,
            "engine": result.get("engine", "llm"),
        }
    )


@router.post("/ai/nl-confirm")
async def nl_confirm(
    payload: NLConfirmIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """把用户在前端核对过的自然语言条目批量入账。

    与 ``/ai/natural`` 的区别：这里不再调 LLM，只落库用户确认后的结果。
    拆成两步是有意的 —— 抽取结果可能错，先给用户看一眼再写账，
    避免 AI 幻觉直接污染账本。
    """
    from ..db.models import TransactionDirection, TransactionSource

    user = await get_or_create_user(session)

    if not payload.items:
        return fail("没有需要入账的条目")

    categories = await ledger_service.list_categories(session, user_id=user.id)
    id_set = {c.id for c in categories}

    from ..services.transaction_validation import validate_references
    resolved = []
    for item in payload.items:
        if item.amount <= 0:
            continue
        if item.direction not in ("expense", "income"):
            return fail("无效的收支方向")
        category_id = _as_int(item.category_id)
        if item.category_id not in (None, "") and category_id not in id_set:
            return fail("类别不存在或无权使用")
        if category_id is None and item.category:
            category_id = next((c.id for c in categories if c.name == item.category and c.direction == item.direction), None)
        account_id = _as_int(item.account_id)
        if item.account_id not in (None, "") and account_id is None:
            return fail("无效的账本编号")
        try:
            await validate_references(session, user_id=user.id, category_id=category_id, account_id=account_id, direction=item.direction)
        except ValueError as exc:
            return fail(str(exc))
        resolved.append((item, category_id, account_id, parse_dt(item.trade_time)))

    created: List[Dict[str, Any]] = []
    for item, category_id, account_id, trade_time in resolved:
        txn = await ledger_service.create_transaction(
            session,
            user_id=user.id,
            amount=item.amount,
            direction=(
                TransactionDirection.EXPENSE
                if (item.direction or "expense") == "expense"
                else TransactionDirection.INCOME
            ),
            category_id=category_id,
            account_id=account_id,
            trade_time=trade_time,
            counterparty=item.counterparty or "",
            memo=item.memo or "",
            raw_input=item.memo or "",
            source=TransactionSource.NATURAL_LANGUAGE,
            confidence=item.confidence,
            is_confirmed=True,
            has_invoice=item.has_invoice,
        )
        created.append(to_dict(txn))
        await audit_service.log_action(session, "create_transaction", user_id=user.id, target_type="transaction", target_id=txn.id, channel="web", detail="自然语言确认入账", extra={"after": to_dict(txn)})

    await audit_service.log_action(
        session,
        "ai_nl_confirm",
        user_id=user.id,
        target_type="transaction",
        operator="user",
        detail=f"确认入账 {len(created)} 条",
        extra={"count": len(created), "after": created},
        channel="web",
    )

    return ok({"created": created, "count": len(created)})


@router.post("/ai/chat")
async def chat(
    payload: ChatIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """对话式查询：函数调用 + 参数抽取，不做裸 NL2SQL。"""
    user = await get_or_create_user(session)

    from ..ai.assistant import answer_question
    from ..ai.tool_query import explicit_question, query_with_tools

    explicit = explicit_question(payload.question)
    result = await (query_with_tools(session, user_id=user.id, question=explicit, history=payload.history) if explicit is not None else answer_question(session, user_id=user.id, question=payload.question, history=payload.history))

    await audit_service.log_action(
        session,
        "ai_chat",
        user_id=user.id,
        target_type="ai",
        operator="ai",
        detail=payload.question[:120],
        extra={"intent": result.get("intent"), "engine": result.get("engine")},
        channel="web",
    )
    return ok(result)


@router.post("/ai/classify")
async def classify(
    payload: ClassifyIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """智能分类：LLM 冷启动 + 用户纠正的 few-shot 示例。"""
    user = await get_or_create_user(session)

    from ..ai.classifier import classify_memo

    categories = await ledger_service.list_categories(session, user_id=user.id)
    result = await classify_memo(session, user_id=user.id, memo=payload.memo, categories=categories)

    if result.get("suggestion"):
        await audit_service.record_suggestion(
            session,
            user_id=user.id,
            kind=SuggestionKind.CATEGORY,
            content=f"将「{payload.memo}」归类为「{result['suggestion'].get('name')}」",
            target_type="memo",
            payload=result,
            confidence=result.get("confidence"),
            reason=result.get("reason"),
        )

    return ok(result)


@router.get("/ai/categories/suggest")
async def suggest_category(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """类别自演化：用户新建类别时建议挂载的税务口径科目。"""
    user = await get_or_create_user(session)

    from ..ai.classifier import suggest_tax_account

    result = await suggest_tax_account(name)
    return ok(result)
