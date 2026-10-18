"""协议端对话式记账与查询插件。

设计原则：**不做裸 NL2SQL**。用户的自然语言先经过 LLM 抽成结构化意图
（``plan.intent`` + 参数），再由白名单函数 ``assistant._execute`` 执行。
这样既可控又好解释，也不会因为一句奇怪的话把数据库读穿。
"""

from __future__ import annotations

import logging
import re

from nonebot import on_command, on_message, on_startswith
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment, PrivateMessageEvent

logger = logging.getLogger("firefly.plugin")

HELP_TEXT = """✨ 流萤 · 财务财税助手

📝 记账
  直接对我说：昨天打车 38，请客户吃饭 260
  /记 餐饮 38 请客户吃饭
  首轮离线解析后回复「接受」入账；「不接受 + 修改意见」交给 AI 重解析；「取消」放弃。

📊 查询
  上月餐饮花了多少？
  这个月一共花了多少？
  我的净资产是多少？

📈 报告
  /报告        生成当期报告
  /报告 周     生成周报
  /税检        税务风险自检

⚙️ 其他
  /明细        最近 10 笔流水
  /预算        本月预算执行情况
  /帮助        显示这条消息

💡 查询类问题用自然语言直接问我就好。"""


# ------------------------------------------------------------------------------
# 自然语言入口
# ------------------------------------------------------------------------------

async def _addressed(event: Event) -> bool:
    from ..ai.tool_query import explicit_question
    return isinstance(event, PrivateMessageEvent) or bool(getattr(event, "to_me", False)) or explicit_question(event.get_plaintext()) is not None


def _text(value):
    return Message(MessageSegment.text(value))


async def _event_user(session, event):
    from ..api.deps import get_or_create_user
    from ..services.bot_conversation import platform_id_for_event
    return await get_or_create_user(session, platform_id=platform_id_for_event(event))


record = on_message(rule=_addressed, priority=20, block=True)


@record.handle()
async def _handle_message(bot: Bot, event: Event) -> None:
    from ..ai.assistant import answer_question
    from ..db.session import session_scope
    from ..services import bot_conversation, audit
    from ..ai.tool_query import explicit_question, query_with_tools
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return
    ai_reply = False
    async with session_scope() as session:
        user = await _event_user(session, event)
        key = bot_conversation.conversation_key(bot.self_id, event.get_session_id(), event.get_user_id())
        explicit = explicit_question(text)
        if explicit is not None:
            result = await query_with_tools(session, user_id=user.id, question=explicit)
            reply = result["answer"]
            ai_reply = result.get("engine") == "llm"
            await audit.log_action(session, "ai_read_query", user_id=user.id, target_type="ai", channel="bot", detail=explicit[:120], extra={"tools": result.get("tools"), "read_only": True})
        else:
            try:
                reply = await bot_conversation.handle_message(session, user_id=user.id, key=key, text=text)
            except ValueError as exc:
                await session.rollback()
                reply = f"未入账：{exc}。请取消本轮并重新发送记账内容。"
        if reply is None:
            result = await answer_question(session, user_id=user.id, question=text)
            reply = result.get("answer") or "请使用 /帮助 查看用法。"
            ai_reply = result.get("engine") == "llm" or result.get("analysis_engine") == "llm"
            await audit.log_action(session, "ai_chat", user_id=user.id, target_type="ai", operator="ai", channel="bot", detail=text[:120], extra={"intent": result.get("intent"), "tools": result.get("tools")})
        await session.commit()
    if ai_reply:
        from ..render.ai_reply import send_ai_reply
        await send_ai_reply(record.send, reply)
        # finish stays outside render/send exception handling (no duplicate fallback replies).
        await record.finish()
        return
    await record.finish(_text(reply))


cmd_record = on_command("记", aliases={"记账", "record"}, priority=5, block=True)


@cmd_record.handle()
async def _cmd_record(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import bot_conversation, ledger
    text = re.sub(r"^/?(?:记账|record|记)\s*", "", event.get_plaintext().strip(), count=1)
    if not text:
        await cmd_record.finish(_text("用法：/记 餐饮 38 请客户吃饭；结果需回复接受后才入账。"))
        return
    async with session_scope() as session:
        user = await _event_user(session, event)
        category = await ledger.get_category_by_name(session, user_id=user.id, name=text.split()[0])
        if category and category.direction == "income":
            text = "收入 " + text
        key = bot_conversation.conversation_key(bot.self_id, event.get_session_id(), event.get_user_id())
        reply = await bot_conversation.handle_message(session, user_id=user.id, key=key, text=text)
        await session.commit()
    await cmd_record.finish(_text(reply or "请补充类别和金额。"))


# ------------------------------------------------------------------------------
# 命令：/报告 [日|周|月|季|年]
# ------------------------------------------------------------------------------

cmd_report = on_command("报告", aliases={"report", "报表"}, priority=5, block=True)


@cmd_report.handle()
async def _cmd_report(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import report as report_service
    from ..api.deps import get_or_create_user

    text = event.get_plaintext()
    period = "monthly"
    if "周" in text:
        period = "weekly"
    elif "季" in text:
        period = "quarterly"
    elif "年" in text:
        period = "yearly"
    elif "日" in text or "天" in text:
        period = "daily"

    await cmd_report.send(Message("⏳ 正在生成报告，请稍候…"))

    async with session_scope() as session:
        user = await _event_user(session, event)
        report = await report_service.generate_report(
            session, user_id=user.id, period=period, persist=True, channel="bot"
        )

    if report.status != "completed":
        await cmd_report.finish(Message(f"❌ 报告生成失败：{report.error_message or '未知错误'}"))
        return

    # 试着出一张图，失败就发文字摘要
    try:
        from nonebot.adapters.onebot.v11 import MessageSegment

        from ..render.html import render_report_html
        from ..render.image import render_html_to_image

        html_path = await render_report_html(report)
        image_path = await render_html_to_image(html_path.read_text(encoding="utf-8"))
        await cmd_report.finish(Message(MessageSegment.image(image_path.resolve().as_uri())))
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告出图失败: %s", exc)
        await cmd_report.finish(Message(report.summary_text or "报告已生成，可在网页查看。"))


# ------------------------------------------------------------------------------
# 命令：/税检
# ------------------------------------------------------------------------------

cmd_taxcheck = on_command("税检", aliases={"税务自检", "tax"}, priority=5, block=True)


@cmd_taxcheck.handle()
async def _cmd_taxcheck(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import ledger as ledger_service
    from ..services import taxcheck
    from ..api.deps import get_or_create_user

    async with session_scope() as session:
        user = await _event_user(session, event)
        start, end = ledger_service.period_bounds("monthly")
        result = await taxcheck.run_tax_check(session, user_id=user.id, start=start, end=end)
        await session.commit()

    labels = {"high": "🔴 高风险", "medium": "🟡 中风险", "low": "🔵 低风险", "info": "ℹ️ 提示"}
    lines = ["🧾 税务风险自检", f"综合等级：{labels.get(result['highest_level'], 'ℹ️ 提示')}"]

    optimizations = result.get("optimizations") or []
    if not optimizations:
        lines.append("\n本期未发现明显税务风险，保持现状即可。")
    else:
        for item in optimizations[:5]:
            lines.append(
                f"\n{labels.get(item['level'], '')} {item['title']}\n"
                f"  {item['description']}\n"
                f"  → {item['suggestion']}"
            )

    if result.get("disclaimer"):
        lines.append(f"\n{result['disclaimer']}")
    await cmd_taxcheck.finish(_text("\n".join(lines)))


# ------------------------------------------------------------------------------
# 命令：/明细
# ------------------------------------------------------------------------------

cmd_list = on_command("明细", aliases={"list", "流水"}, priority=5, block=True)


@cmd_list.handle()
async def _cmd_list(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import ledger as ledger_service
    from ..api.deps import get_or_create_user

    async with session_scope() as session:
        user = await _event_user(session, event)
        items = await ledger_service.list_transactions(
            session, user_id=user.id, limit=10, order_desc=True
        )

    lines = ["📒 最近 10 笔流水"]
    if not items:
        lines.append("还没有记账记录。对我说「今天午饭 25」试试。")
    for txn in items:
        sign = "＋" if txn.direction == "income" else "－"
        memo = f" {txn.memo}" if txn.memo else ""
        lines.append(f"{txn.trade_time.strftime('%m-%d %H:%M')} {sign}{txn.amount:.2f}{memo}")

    await cmd_list.finish(_text("\n".join(lines)))


# ------------------------------------------------------------------------------
# 命令：/预算
# ------------------------------------------------------------------------------

cmd_budget = on_command("预算", aliases={"budget"}, priority=5, block=True)


@cmd_budget.handle()
async def _cmd_budget(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import budget as budget_service
    from ..services import ledger as ledger_service
    from ..api.deps import get_or_create_user

    async with session_scope() as session:
        user = await _event_user(session, event)
        start, end = ledger_service.period_bounds("monthly")
        status = await budget_service.get_budget_status(
            session, user_id=user.id, start=start, end=end, period="monthly"
        )

    if not status["has_budget"]:
        await cmd_budget.finish(Message("本月还没设置预算，可以在网页的「预算」页添加。"))
        return

    lines = [
        f"💰 本月预算 {status['total_budget']:.2f} 元",
        f"已用 {status['total_actual']:.2f} 元（{status['total_usage_ratio'] * 100:.1f}%）",
        f"剩余 {status['total_remaining']:.2f} 元",
    ]
    for item in status["items"]:
        if item["state"] in ("over", "warn"):
            lines.append(
                f"{'🔴' if item['state'] == 'over' else '🟡'} "
                f"{item['name']} {item['usage_ratio'] * 100:.0f}%"
            )

    await cmd_budget.finish(_text("\n".join(lines)))


# ------------------------------------------------------------------------------
# 命令：/帮助 与 打招呼
# ------------------------------------------------------------------------------

cmd_help = on_command("帮助", aliases={"help", "菜单"}, priority=5, block=True)


@cmd_help.handle()
async def _cmd_help(bot: Bot, event: Event) -> None:
    await cmd_help.finish(Message(HELP_TEXT))


greet = on_startswith(("你好", "hi", "hello", "在吗"), ignorecase=True, priority=30, block=False)


@greet.handle()
async def _greet(bot: Bot, event: Event) -> None:
    await greet.finish(Message("你好呀，我是流萤 ✨\n" + HELP_TEXT))
