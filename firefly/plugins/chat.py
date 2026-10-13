"""协议端对话式记账与查询插件。

设计原则：**不做裸 NL2SQL**。用户的自然语言先经过 LLM 抽成结构化意图
（``plan.intent`` + 参数），再由白名单函数 ``assistant._execute`` 执行。
这样既可控又好解释，也不会因为一句奇怪的话把数据库读穿。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from nonebot import on_command, on_message, on_startswith
from nonebot.adapters.onebot.v11 import Bot, Event, Message
from nonebot.rule import to_me

logger = logging.getLogger("firefly.plugin")

HELP_TEXT = """✨ 流萤 · 财务财税助手

📝 记账
  直接对我说：昨天打车 38，请客户吃饭 260
  /记 餐饲 38 请客户吃饭

📊 查询
  上月餐饲花了多少？
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

record = on_message(rule=to_me(), priority=20, block=False)


@record.handle()
async def _handle_message(bot: Bot, event: Event) -> None:
    """把用户说的话交给意图规划器：记账 / 查询 / 不懂。"""
    from ..ai.assistant import answer_question, rule_plan
    from ..ai.natural import extract_transactions
    from ..db.session import session_scope
    from ..services import ledger as ledger_service
    from ..api.deps import get_or_create_user

    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return

    # 先判断是不是在记账：带金额的句子交给抽取器
    plan = rule_plan(text)
    looks_like_record = bool(re.search(r"\d", text)) and plan.get("intent") in ("unknown", None)

    async with session_scope() as session:
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
        await ledger_service.ensure_default_categories(session, user_id=user.id)
        categories = [
            item.name
            for item in await ledger_service.list_categories(session, user_id=user.id)
        ]

        if looks_like_record:
            extracted = await extract_transactions(text, categories=categories)
            if extracted["success"]:
                lines = await _persist_items(session, user.id, extracted["items"])
                engine_label = "AI" if extracted["engine"] == "llm" else "规则"
                await session.commit()
                await record.finish(
                    Message(
                        f"✅ 已记账（{engine_label}解析）\n" + "\n".join(lines)
                        + (f"\n\n💬 {extracted['message']}" if extracted.get("message") else "")
                    )
                )
                return

        # 否则当成查询
        result = await answer_question(session, user_id=user.id, question=text)
        await session.commit()

    answer = result.get("answer") or result.get("message") or ""
    if not answer:
        answer = "这个问题我还答不上来，试试 /帮助 看看我会什么～"
    await record.finish(Message(answer))


async def _persist_items(session, user_id: str, items) -> list:
    """把抽取结果落库，返回给用户看的确认行。"""
    from ..services import ledger as ledger_service

    lines = []
    for item in items:
        category = None
        if item.get("category"):
            category = await ledger_service.get_category_by_name(
                session, user_id=user_id, name=item["category"]
            )
        if category is None:
            category = await ledger_service.get_category_by_name(
                session, user_id=user_id, name="其他支出"
            )

        txn = await ledger_service.create_transaction(
            session,
            user_id=user_id,
            amount=item.get("amount") or 0,
            direction=item.get("direction") or "expense",
            category_id=category.id if category else None,
            trade_time=_parse_time(item.get("trade_time")),
            counterparty=item.get("counterparty"),
            memo=item.get("memo"),
            raw_input=item.get("raw") or "",
            source="natural_language",
            confidence=float(item.get("confidence") or 0.0),
            is_confirmed=True,
        )
        sign = "＋" if txn.direction == "income" else "－"
        memo = f"（{txn.memo}）" if txn.memo else ""
        lines.append(f"{sign}{txn.amount:.2f} · {category.name if category else '未分类'}{memo}")

    if len(lines) > 1:
        total = sum(abs(float(item.get("amount") or 0)) for item in items)
        lines.append(f"共 {len(lines)} 笔，合计 {total:.2f} 元")

    return lines


def _parse_time(value: Optional[str]) -> datetime:
    """解析抽取器给出的时间；失败就用现在。"""
    if not value:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m-%d %H:%M", "%m-%d"):
        try:
            parsed = datetime.strptime(str(value), fmt)
            if fmt.startswith("%m"):
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue
    return datetime.now()


# ------------------------------------------------------------------------------
# 命令：/记 类别 金额 [备注]
# ------------------------------------------------------------------------------

cmd_record = on_command("记", aliases={"记账", "record"}, priority=5, block=True)


@cmd_record.handle()
async def _cmd_record(bot: Bot, event: Event) -> None:
    from ..db.session import session_scope
    from ..services import ledger as ledger_service
    from ..api.deps import get_or_create_user

    parts = [part for part in event.get_plaintext().split() if not part.startswith("/")]
    if parts and parts[0] in ("记", "记账", "record"):
        parts = parts[1:]
    if len(parts) < 2:
        await cmd_record.finish(Message("用法：/记 类别 金额 [备注]，例如 /记 餐饲 38 请客户吃饭"))
        return

    category_name = parts[0]
    amount_match = re.search(r"\d+(?:\.\d+)?", parts[1])
    if not amount_match:
        await cmd_record.finish(Message("金额没看懂，请写成数字，例如 /记 餐饲 38"))
        return

    amount = float(amount_match.group())
    memo = " ".join(parts[2:]) if len(parts) > 2 else None

    async with session_scope() as session:
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
        await ledger_service.ensure_default_categories(session, user_id=user.id)
        category = await ledger_service.get_category_by_name(
            session, user_id=user.id, name=category_name
        )
        if category is None:
            category = await ledger_service.get_category_by_name(
                session, user_id=user.id, name="其他支出"
            )

        txn = await ledger_service.create_transaction(
            session,
            user_id=user.id,
            amount=amount,
            direction=category.direction if category else "expense",
            category_id=category.id if category else None,
            # 带时区：SQLModel 的 DateTime 拒绝 naive 值
            trade_time=datetime.now(timezone.utc),
            memo=memo,
            raw_input=event.get_plaintext(),
            source="manual",
            confidence=1.0,
            is_confirmed=True,
        )
        await session.commit()
        display = category.name if category else "未分类"

    await cmd_record.finish(
        Message(f"✅ 已记账：{display} {txn.amount:.2f} 元" + (f"（{memo}）" if memo else ""))
    )


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
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
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
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
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
    await cmd_taxcheck.finish(Message("\n".join(lines)))


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
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
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

    await cmd_list.finish(Message("\n".join(lines)))


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
        user = await get_or_create_user(session, platform_id=str(event.get_user_id()))
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

    await cmd_budget.finish(Message("\n".join(lines)))


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
