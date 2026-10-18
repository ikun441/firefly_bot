"""QQ drafts: offline preview → explicit acceptance, or AI retry → preview.

No protocol calls here. Draft state and created transactions share a DB transaction.
A compare-and-swap revision prevents repeated/concurrent acceptance from double posting.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from sqlalchemy import update
from sqlmodel import select
from ..ai.natural import rule_extract, refine_transactions
from ..db.models import BotDraft, TransactionDirection, TransactionSource
from . import ledger, audit

ACCEPT = {"接受", "确认", "确认入账", "同意", "是", "确认记账"}
CANCEL = {"取消", "取消记账", "放弃"}
REJECT = {"不接受", "不对", "否", "重新解析", "重试", "拒绝"}


def conversation_key(bot_id, session_id, sender_id):
    return hashlib.sha256(json.dumps([str(bot_id), str(session_id), str(sender_id)]).encode()).hexdigest()


def platform_id_for_event(event):
    """Only configured owners in private chat can access the local web ledger."""
    from ..config import get_config
    cfg = get_config()
    sender = str(event.get_user_id())
    owners = {str(x) for x in cfg.onebot.superusers}
    if cfg.push.target_type == "private" and cfg.push.target_id:
        owners.add(str(cfg.push.target_id))
    private = getattr(event, "message_type", None) == "private"
    if private and sender in owners:
        return "local"
    # Other QQ users and group conversations get separate user-scoped ledgers.
    group = str(getattr(event, "group_id", ""))
    return "qq:" + hashlib.sha256(f"{group}:{sender}".encode()).hexdigest()[:48]


def looks_like_record(text):
    if re.search(r"(多少|几笔|明细|查询|查一下|统计|分析|预算|排行|列出|对比|超过|低于|大于|小于|有没有)", text):
        return False
    return bool(re.search(r"\d|[一二两三四五六七八九十百千万]+[元块]", text))


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def normalize_items(session, user_id, items):
    if not items or len(items) > 100:
        raise ValueError("请每次提交 1–100 笔流水")
    categories = await ledger.list_categories(session, user_id=user_id)
    accounts = await ledger.list_accounts(session, user_id=user_id)
    if not accounts:
        raise ValueError("请先创建账本")
    result = []
    for item in items:
        try:
            amount = Decimal(str(item.get("amount")))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("金额无效") from exc
        if not amount.is_finite() or not 0 < amount <= 100_000_000 or amount.as_tuple().exponent < -2:
            raise ValueError("金额必须为不超过 1 亿元、最多两位小数的正数")
        direction = item.get("direction", "expense")
        if direction not in ("income", "expense"):
            raise ValueError("收支方向无效")
        category = next((c for c in categories if c.name == item.get("category") and c.direction == direction), None)
        if category is None:
            category = next((c for c in categories if c.name in str(item.get("memo") or "") and c.direction == direction), None)
        if category is None:
            category = next((c for c in categories if c.name == ("其他收入" if direction == "income" else "其他支出")), None)
        value = item.get("trade_time")
        trade_time = value if isinstance(value, datetime) else datetime.fromisoformat(str(value)) if value else None
        result.append({"amount": float(amount), "direction": direction, "category_id": category.id if category else None,
                       "category": category.name if category else "未分类", "account_id": accounts[0].id, "account_name": accounts[0].name,
                       "trade_time": ledger.ensure_aware(trade_time).isoformat(), "memo": str(item.get("memo") or "")[:512],
                       "counterparty": str(item.get("counterparty") or "")[:128], "has_invoice": item.get("has_invoice") is True})
    return result


def preview(draft):
    lines = ["📝 待确认（离线规则解析）" if draft.engine == "rule" else "📝 待确认（AI 结构化解析）"]
    for i, item in enumerate(draft.items, 1):
        lines.append(f"{i}. {'收入' if item['direction']=='income' else '支出'} {item['amount']:.2f} 元 · {item['category']} · {'有发票' if item['has_invoice'] else '无发票'}\n   {item['trade_time']} · {item['account_name']} · {item['memo']}")
    lines.append("尚未入账。回复「接受」才保存；回复「不接受 + 修改意见」交给 AI 重解析，也可回复「取消」。本轮确认 15 分钟内有效。")
    return "\n".join(lines)


async def _claim(session, draft, state):
    result = await session.execute(update(BotDraft).where(BotDraft.id == draft.id, BotDraft.revision == draft.revision, BotDraft.state == draft.state).values(state=state, revision=draft.revision + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        return False
    await session.refresh(draft)
    return True


async def handle_message(session, *, user_id, key, text):
    """Return a reply, or None to let the caller run a read-only AI query."""
    text = text.strip()[:4000]
    now = datetime.now(timezone.utc)
    draft = (await session.exec(select(BotDraft).where(BotDraft.session_key == key, BotDraft.user_id == user_id))).first()
    active = draft is not None and draft.state in {"pending", "needs_revision", "refining"}
    if active and _aware(draft.expires_at) <= now:
        draft.state = "expired"
        session.add(draft)
        active = False
        if text in ACCEPT | CANCEL | REJECT:
            return "待确认记录已过期，未入账，请重新发送记账内容。"
    if active:
        if text in CANCEL:
            if not await _claim(session, draft, "cancelled"):
                return "本轮状态已变化，请重新查看。"
            await audit.log_action(session, "bot_record_cancel", user_id=user_id, target_type="bot_draft", target_id=draft.id, channel="bot", detail="用户取消待确认记账")
            return "已取消，未写入流水。"
        if text in ACCEPT:
            if draft.state != "pending" or not draft.items:
                return "当前没有可接受的结果，请补充修改意见重新解析，或回复取消。"
            if not await _claim(session, draft, "accepted"):
                return "该结果已经处理，请勿重复确认。"
            from .transaction_validation import validate_references
            for item in draft.items:
                await validate_references(session, user_id=user_id, category_id=item["category_id"], account_id=item["account_id"], direction=item["direction"])
            ids = []
            for item in draft.items:
                txn = await ledger.create_transaction(session, user_id=user_id, amount=item["amount"], direction=TransactionDirection(item["direction"]), category_id=item["category_id"], account_id=item["account_id"], trade_time=datetime.fromisoformat(item["trade_time"]), memo=item["memo"], counterparty=item["counterparty"], raw_input=draft.original, has_invoice=item["has_invoice"], source=TransactionSource.NATURAL_LANGUAGE, is_confirmed=True)
                ids.append(txn.id)
                await audit.log_action(session, "create_transaction", user_id=user_id, target_type="transaction", target_id=txn.id, channel="bot", detail="QQ 用户接受后记账", extra={"draft_id": draft.id, "after": json.loads(txn.model_dump_json())})
            return f"✅ 已按你接受的结果入账 {len(ids)} 笔。"
        if draft.state == "refining":
            return "AI 正在重新解析，请稍候再确认。"
        if not await _claim(session, draft, "refining"):
            return "本轮状态已变化，请重新查看。"
        # Persist rejection before an external LLM call, preventing acceptance of rejected items.
        draft.feedback = (draft.feedback + [text])[-10:]
        session.add(draft)
        await audit.log_action(session, "bot_record_reject", user_id=user_id, target_type="bot_draft", target_id=draft.id, channel="bot", detail=text[:512])
        await session.commit()
        revision = draft.revision
        try:
            categories = await ledger.list_categories(session, user_id=user_id)
            items = await refine_transactions(draft.original, draft.items, draft.feedback, [c.name for c in categories])
            normalized = await normalize_items(session, user_id, items)
        except Exception as exc:
            await session.refresh(draft)
            if draft.revision == revision and draft.state == "refining":
                draft.state = "needs_revision"
                session.add(draft)
            from ..ai.llm import safe_error
            return "AI 重解析未成功，未入账。" + safe_error(exc) + "。补充修改意见后重试，或回复取消。"
        await session.refresh(draft)
        if draft.revision != revision or draft.state != "refining":
            return "本轮已取消或发生变化，未入账。"
        draft.items = normalized
        draft.engine = "llm"
        draft.state = "pending"
        draft.expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        session.add(draft)
        return preview(draft)
    if text in ACCEPT | CANCEL | REJECT:
        return "没有待确认的记录，未新增流水。请先发送记账内容。"
    if not looks_like_record(text):
        return None
    try:
        items = rule_extract(text)
        if not items:
            return "未识别到有效记账数据，请补充金额和用途，例如「今天打车 38 元」。"
        normalized = await normalize_items(session, user_id, items)
    except ValueError:
        return "记账数据无效，未入账。请检查日期和金额（正数、最多两位小数、最高 1 亿元）。"
    if draft is None:
        draft = BotDraft(session_key=key, user_id=user_id)
    draft.original = text
    draft.items = normalized
    draft.feedback = []
    draft.engine = "rule"
    draft.state = "pending"
    draft.revision += 1
    draft.expires_at = now + timedelta(minutes=15)
    session.add(draft)
    await session.flush()
    await audit.log_action(session, "bot_record_preview", user_id=user_id, target_type="bot_draft", target_id=draft.id, channel="bot", detail="离线规则结果待确认")
    return preview(draft)
