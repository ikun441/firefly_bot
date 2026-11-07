"""QQ commands with live CRUD authorization and attributable accounting operations."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from ..accounting.api import transaction
from ..accounting.identity import Actor, acting_as, require, current_actor
from ..accounting.domain import AccountingError, minor
from ..accounting import service as svc
from ..accounting.models import Conversation
from ..accounting.help import help_markdown


def actor_for(bot, event):
    return Actor(
        "qq",
        str(event.get_user_id()),
        "qq",
        str(getattr(event, "group_id", "")),
        str(bot.self_id),
    )


async def addressed(event: Event):
    from ..ai.tool_query import explicit_question

    return (
        isinstance(event, PrivateMessageEvent)
        or bool(getattr(event, "to_me", False))
        or explicit_question(event.get_plaintext()) is not None
    )


def txt(value):
    return Message(MessageSegment.text(value))


def session_key(bot, event):
    return hashlib.sha256(
        json.dumps(
            [str(bot.self_id), event.get_session_id(), event.get_user_id()]
        ).encode()
    ).hexdigest()


async def get_book(s):
    book = await svc.active_book(s)
    if not book:
        raise AccountingError("BOOK_REQUIRED", "请由管理员先在网页创建会计账套")
    return book


async def record_text(bot, event, text):
    from ..ai.natural import rule_extract, refine_transactions

    key = session_key(bot, event)
    who = current_actor.get().key
    now = datetime.now(timezone.utc)
    refine = None
    async with transaction() as s:
        await require(s, "create")
        book = await get_book(s)
        c = await s.get(Conversation, key)
        if c and c.owner != who:
            raise AccountingError("FORBIDDEN", "草稿不属于当前账号", 403)
        expiry = (
            c.expires_at.replace(tzinfo=timezone.utc)
            if c and c.expires_at.tzinfo is None
            else c.expires_at
            if c
            else now
        )
        active = c and c.state == "pending" and expiry > now
        if text in {"接受", "确认", "确认入账"}:
            if not active:
                return "当前没有有效待确认内容，未新增账目。"
            draft = await svc.business_draft(
                s, book.id, f"qq:{key}:{c.revision}", c.facts
            )
            c.state = "accepted"
            s.add(c)
            svc.event(s, "confirm_business", book_id=book.id, draft_id=draft.id)
            return f"已保存业务草稿 #{draft.id}，待制证审核，尚未过账。"
        if text in {"取消", "取消记账"}:
            if c:
                c.state = "cancelled"
                s.add(c)
            svc.event(s, "cancel_conversation", book_id=book.id)
            return "已取消待确认业务，未过账。"
        if text.startswith(("不接受", "不对")):
            if not active:
                return "没有可修改的有效草稿，请重新发送业务。"
            # Persist rejection before external parsing. Revocation is checked again afterwards.
            c.state = "refining"
            c.revision += 1
            s.add(c)
            refine = (
                c.revision,
                c.facts.get("original", ""),
                c.facts.get("items", []),
                book.id,
            )
            svc.event(s, "reject_business", book_id=book.id, reason=text[:512])
        else:
            items = rule_extract(text)
            if not items:
                return "请补充用途与金额，例如 /记 办公费 200；或使用 /帮助。"
            for item in items:
                minor(str(item.get("amount")))
            facts = {"original": text, "items": items, "source": "qq"}
            facts = json.loads(json.dumps(facts, default=str))
            if c is None:
                c = Conversation(session_key=key, owner=who)
            c.facts = facts
            c.state = "pending"
            c.revision += 1
            c.expires_at = now + timedelta(minutes=15)
            s.add(c)
            svc.event(s, "preview_business", book_id=book.id)
            return preview(facts)
    if refine:
        revision, original, items, book_id = refine
        try:
            corrected = await refine_transactions(original, items, [text], [])
            for item in corrected:
                minor(str(item.get("amount")))
            if not corrected:
                raise ValueError("empty result")
        except Exception:
            return "重新解析未完成，拒绝的草稿不会入账；请重新发送业务。"
        async with transaction() as s:
            await require(s, "create")
            c = await s.get(Conversation, key)
            if c.revision != revision or c.state != "refining":
                return "草稿已变化，请重新发送业务。"
            c.facts = json.loads(
                json.dumps(
                    {"original": original, "items": corrected, "source": "qq"},
                    default=str,
                )
            )
            c.state = "pending"
            c.expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            s.add(c)
            svc.event(s, "refine_business", book_id=book_id)
            return preview(c.facts)


def preview(facts):
    rows = ["待确认业务（尚未制证 / 过账）"]
    for item in facts["items"]:
        rows.append(
            f"{item.get('memo', '')} · {item.get('amount')} 元 · "
            + {'expense': '支出', 'income': '收入', 'transfer': '转账'}.get(item.get('direction'), '待确认')
        )
    rows.append(
        "回复「接受」保存业务草稿；「不接受 + 修改意见」重新解析；「取消」放弃。15 分钟有效。"
    )
    return "\n".join(rows)


async def dispatch(bot, event, command, args):
    with acting_as(actor_for(bot, event)):
        async with transaction() as audit_session:
            svc.event(audit_session,"qq_command_request",command=command)
        if command == "记":
            return await record_text(bot, event, args)
        if command == "AI问询":
            from ..accounting.ai import query

            async with transaction() as s:
                await require(s, "read")
                book = await get_book(s)
                book_id = book.id
            return (await query(book_id, args))["answer"]
        async with transaction() as s:
            if command == "帮助":
                return await help_markdown(s)
            permission = (
                "update"
                if command in {"提交", "修改摘要"}
                else "delete"
                if command in {"作废", "冲销"}
                else "admin"
                if command in {"审核", "过账"}
                else "read"
            )
            await require(s, permission)
            book = await get_book(s)
            if command == "明细":
                from ..accounting.periods import parse_detail_args
                from ..accounting.queries import list_vouchers

                start, end, page = parse_detail_args(args)
                result = await list_vouchers(
                    s, book.id, start=start, end=end, offset=(page - 1) * 10, limit=10
                )
                states = {
                    "draft": "草稿",
                    "submitted": "待审核",
                    "approved": "已审核",
                    "posted": "已过账",
                    "void": "已作废",
                }
                lines = [
                    f"# 凭证明细 · {book.name}",
                    f"{start} 至 {end} · 共 {result['total']} 张 · 第 {page} 页（每页10张，含未过账状态）",
                    "",
                ]
                for v in result["rows"]:
                    lines.append(
                        f"- {v['accounting_date']} · #{v['id']} · {v['number'] or '未编号'} · {states[v['state']]} · {v['summary']}"
                    )
                if not result["rows"]:
                    lines.append("本页没有凭证。")
                if result["offset"] + len(result["rows"]) < result["total"]:
                    lines.append(f"\n下一页：/明细 {start} {end} 第{page + 1}页")
                return "\n".join(lines)
            if command == "余额":
                result = await svc.trial_balance(s, book.id)
                svc.event(s, "qq_read_balances", book_id=book.id)
                return (
                    "科目余额（已过账）\n"
                    + "\n".join(
                        f"{r['code']} {r['name']} 借贷净额 {r['balance']}"
                        for r in result["rows"]
                        if r["balance_minor"]
                    )
                    + ('\n试算平衡：通过' if result['balanced'] else '\n试算平衡：不通过')
                )
            if command in {"报告", "查询"}:
                from ..accounting.periods import parse_range

                start, end = parse_range(args)
                result = await svc.reports(s, book.id, start, end)
                return report_markdown(result)
            if command == "预算":
                from ..accounting.budgets import overview
                month = args.strip() or date.today().strftime("%Y-%m")
                result = await overview(s, book.id, month)
                rows = [f"# 费用预算 · {book.name}", f"{month} · {result['basis']}", "",
                        f"预算合计：{result['planned']} 元；实际费用：{result['used']} 元", ""]
                rows.extend(f"- {c['name']}：预算 {c['budget'] or '未设置'}；已用 {c['used']} 元；{c['state']}" for c in result['categories'])
                if not result['categories']:
                    rows.append("尚未设置费用类别，请在网页预算管理中添加。")
                return "\n".join(rows)
            if command == "税检":
                return "新会计口径的税检模块尚在重构中，旧流水估算不作为会计结论。"
            parts = args.split(maxsplit=1)
            if not parts or not parts[0].isdigit():
                return f"用法：/{command} 凭证ID" + (
                    " 原因" if command == "作废" else ""
                )
            vid = int(parts[0])
            v = await svc.get_voucher(s, book.id, vid)
            reason = parts[1] if len(parts) > 1 else ""
            if command == "冲销":
                reverse_args = reason.split(maxsplit=1)
                if len(reverse_args) != 2:
                    return "用法：/冲销 凭证ID YYYY-MM-DD 原因"
                result = await svc.reversal(
                    s,
                    book.id,
                    vid,
                    date.fromisoformat(reverse_args[0]),
                    reverse_args[1],
                )
            elif command == "修改摘要":
                if not reason:
                    return "用法：/修改摘要 凭证ID 新摘要"
                data = await svc.detail(s, vid, book.id)
                result = await svc.update_voucher(
                    s,
                    book.id,
                    vid,
                    dict(
                        accounting_date=data["accounting_date"],
                        summary=reason,
                        expected_version=v.version,
                        lines=[
                            {
                                k: x[k]
                                for k in [
                                    "account_id",
                                    "side",
                                    "amount",
                                    "auxiliary",
                                    "cashflow",
                                    "summary",
                                    "budget_category_id",
                                    "quantity",
                                    "unit_price",
                                    "unit",
                                ]
                            }
                            for x in data["lines"]
                        ],
                    ),
                )
            else:
                if command == "作废" and not reason.strip():
                    return "请填写作废原因。"
                actions = {
                    "提交": "submit",
                    "审核": "approve",
                    "过账": "post",
                    "作废": "void",
                }
                result = await svc.action(
                    s, book.id, vid, actions[command], v.version, reason
                )
            return (
                f"凭证 #{result['id']} · {result['summary']} · "
                + {'draft': '草稿', 'submitted': '待审核', 'approved': '已审核', 'posted': '已过账', 'void': '已作废'}.get(result['state'], '处理中')
            )


def report_markdown(result):
    """Human-readable statement for QQ image rendering; never expose raw objects."""
    balance, income, cash = result["balance_sheet"], result["income_statement"], result["cash_flow"]
    rows = [f"# 会计汇总 · {result['book']}",
            f"{result['start']} 至 {result['end']} · 仅已过账凭证", "",
            "## 资产负债与权益", f"- 资产：{balance['assets']} 元",
            f"- 负债：{balance['liabilities']} 元", f"- 权益：{balance['equity']} 元",
            "- 勾稽检查：" + ("通过" if balance["balanced"] else "不通过"), "",
            "## 本期损益", f"- 收入：{income['income']} 元", f"- 费用：{income['expense']} 元",
            f"- 利润：{income['profit']} 元", "", "## 现金流量",
            f"- 资金净变动：{cash['cash_change']} 元"]
    if cash["complete"]:
        rows.extend(f"- {name}：{cash['items'][key]} 元" for key, name in
                    [("operating", "经营活动"), ("investing", "投资活动"), ("financing", "筹资活动")])
    else:
        rows.append("部分凭证的现金流项目待补全，暂不出具完整现金流量汇总。")
        rows.append("待检查凭证：" + "、".join(str(v) for v in cash["unclassified_voucher_ids"]))
    if not result["opening_finalized"]:
        rows.extend(["", "期初尚未核定，请先完成期初审核。"])
    rows.extend(["", result["notice"]])
    return "\n".join(rows)


def register(name, aliases=()):
    matcher = on_command(name, aliases=set(aliases), priority=5, block=True)

    @matcher.handle()
    async def handler(bot: Bot, event: Event):
        raw = event.get_plaintext().strip()
        args = re.sub(r"^/?\S+\s*", "", raw, count=1)
        try:
            result = await dispatch(bot, event, name, args)
        except (AccountingError, ValueError) as exc:
            result = str(exc)
        # finish raises FinishedException; do not put it in the rendering try block.
        if name in {"帮助", "明细", "报告", "预算", "AI问询"}:
            from ..render.ai_reply import send_ai_reply

            await send_ai_reply(
                matcher.send,
                result,
                title={
                    "帮助": "流萤 · 命令帮助",
                    "明细": "流萤 · 凭证明细",
                    "报告": "流萤 · 会计报告",
                }.get(name, "流萤 · 会计问询"),
            )
            await matcher.finish()
        else:
            await matcher.finish(txt(result))

    return matcher


cmd_ai = register("AI问询", ("ai", "AI"))
cmd_help = register("帮助", ("help", "菜单"))
cmd_record = register("记", ("记账", "record"))
cmd_list = register("明细", ("流水", "list"))
cmd_balance = register("余额")
cmd_report = register("报告", ("report", "报表"))
cmd_submit = register("提交")
cmd_edit = register("修改摘要")
cmd_void = register("作废")
cmd_reverse = register("冲销")
cmd_review = register("审核")
cmd_post = register("过账")
cmd_taxcheck = register("税检", ("tax",))
cmd_budget = register("预算", ("budget",))

record = on_message(rule=addressed, priority=20, block=True)


@record.handle()
async def handle_message(bot: Bot, event: Event):
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return
    with acting_as(actor_for(bot, event)):
        try:
            from ..ai.tool_query import explicit_question

            question = explicit_question(text)
            if question is not None:
                reply = await dispatch(bot, event, "AI问询", question)
            else:
                reply = await record_text(bot, event, text)
        except (AccountingError, ValueError) as exc:
            reply = str(exc)
    if question is not None:
        from ..render.ai_reply import send_ai_reply

        await send_ai_reply(record.send, reply)
        await record.finish()
    else:
        await record.finish(txt(reply))
