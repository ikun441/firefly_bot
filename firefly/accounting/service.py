"""Accounting use cases. The caller owns one serialized database transaction."""

from datetime import date
import hashlib
import json
from sqlalchemy import delete
from sqlmodel import select
from .domain import AccountingError, balanced, minor, money
from .identity import current_actor, require
from .models import (
    AccountingEvent,
    Book,
    BusinessDraft,
    ChartAccount,
    CommandReceipt,
    Period,
    Voucher,
    VoucherLine,
)

# Selected common accounts from the small-enterprise template. Additional leaf accounts
# can be created explicitly; this is not a claim of complete industry coverage.
ACCOUNTS = [
    ("1001", "库存现金", "asset", "debit", True),
    ("1002", "银行存款", "asset", "debit", True),
    ("1122", "应收账款", "asset", "debit", False),
    ("1123", "预付账款", "asset", "debit", False),
    ("1221", "其他应收款", "asset", "debit", False),
    ("1403", "原材料", "asset", "debit", False),
    ("1405", "库存商品", "asset", "debit", False),
    ("1601", "固定资产", "asset", "debit", False),
    ("1602", "累计折旧", "asset", "credit", False),
    ("2001", "短期借款", "liability", "credit", False),
    ("2202", "应付账款", "liability", "credit", False),
    ("2203", "预收账款", "liability", "credit", False),
    ("2211", "应付职工薪酬", "liability", "credit", False),
    ("2221", "应交税费", "liability", "credit", False),
    ("2241", "其他应付款", "liability", "credit", False),
    ("3001", "实收资本", "equity", "credit", False),
    ("3103", "本年利润", "equity", "credit", False),
    ("3104", "利润分配", "equity", "credit", False),
    ("5001", "主营业务收入", "income", "credit", False),
    ("5051", "其他业务收入", "income", "credit", False),
    ("5301", "营业外收入", "income", "credit", False),
    ("5401", "主营业务成本", "expense", "debit", False),
    ("5402", "其他业务成本", "expense", "debit", False),
    ("5403", "税金及附加", "expense", "debit", False),
    ("5601", "销售费用", "expense", "debit", False),
    ("5602", "管理费用", "expense", "debit", False),
    ("5603", "财务费用", "expense", "debit", False),
    ("5711", "营业外支出", "expense", "debit", False),
    ("5801", "所得税费用", "expense", "debit", False),
]


def event(session, action, *, book_id=None, voucher_id=None, **detail):
    session.add(
        AccountingEvent(
            book_id=book_id,
            voucher_id=voucher_id,
            action=action,
            actor=current_actor.get().snapshot(),
            detail=detail,
        )
    )


async def get_book(session, book_id):
    book = await session.get(Book, book_id)
    if book is None:
        raise AccountingError("BOOK_NOT_FOUND", "账套不存在", 404)
    return book


async def period(session, book_id, day):
    book = await writable_book(session, book_id)
    if day < book.starts_on:
        raise AccountingError("BEFORE_START", "会计日期早于账套启用日")
    month = day.strftime("%Y-%m")
    p = (
        await session.exec(
            select(Period).where(Period.book_id == book_id, Period.month == month)
        )
    ).first()
    if p and p.closed:
        raise AccountingError("PERIOD_CLOSED", "期间已结账，不能新增或更改账务", 409)
    if p is None:
        p = Period(book_id=book_id, month=month)
        session.add(p)
        await session.flush()
    return p


async def create_book(session, name, starts_on):
    await require(session, "admin")
    if starts_on.day != 1:
        raise AccountingError("START_DATE", "请使用月初作为启用日")
    book = Book(name=name.strip(), starts_on=starts_on)
    if not book.name:
        raise AccountingError("NAME_REQUIRED", "请填写账套名称")
    session.add(book)
    await session.flush()
    for code, title, kind, side, cash in ACCOUNTS:
        session.add(
            ChartAccount(
                book_id=book.id,
                code=code,
                name=title,
                kind=kind,
                normal_side=side,
                cash=cash,
            )
        )
    from .models import AccessControl

    control = await session.get(AccessControl, 1)
    control.active_book_id = book.id
    session.add(control)
    event(session, "create_book", book_id=book.id, name=book.name)
    await session.flush()
    return book.model_dump(mode="json")


async def accounts(session, book_id):
    return list(
        (
            await session.exec(
                select(ChartAccount)
                .where(ChartAccount.book_id == book_id)
                .order_by(ChartAccount.code)
            )
        ).all()
    )


async def create_account(session, book_id, data):
    from .subjects import create_account as create

    return await create(session, book_id, data)


async def lines_for(session, voucher_id):
    return list(
        (
            await session.exec(
                select(VoucherLine)
                .where(VoucherLine.voucher_id == voucher_id)
                .order_by(VoucherLine.id)
            )
        ).all()
    )


async def detail(session, voucher_id, book_id):
    v = await session.get(Voucher, voucher_id)
    if v is None or v.book_id != book_id:
        raise AccountingError("VOUCHER_NOT_FOUND", "凭证不存在", 404)
    data = v.model_dump(mode="json")
    data["lines"] = [
        {**x.model_dump(), "amount": money(x.amount_minor)}
        for x in await lines_for(session, v.id)
    ]
    return data


async def validate_lines(session, book_id, inputs, *, reversal=False, kind="ordinary"):
    mapping = {a.id: a for a in await accounts(session, book_id)}
    from .dimensions import line_dimensions

    result = []
    parents = {a.parent_id for a in mapping.values() if a.parent_id}
    for x in inputs:
        a = mapping.get(x.get("account_id"))
        if not a or (not a.enabled and not reversal):
            raise AccountingError("ACCOUNT_INVALID", "科目不存在、已停用或不属于该账套")
        if (
            a.id in parents
            and not reversal
            and kind not in {"closing", "annual_closing"}
        ):
            raise AccountingError(
                "LEAF_REQUIRED", "请选择末级科目，上级科目不能新增记账"
            )
        ancestor = a
        while ancestor.parent_id and not reversal:
            ancestor = mapping.get(ancestor.parent_id)
            if not ancestor or not ancestor.enabled:
                raise AccountingError("ACCOUNT_DISABLED", "上级科目已停用")
        side = x.get("side")
        if side not in {"debit", "credit"}:
            raise AccountingError("SIDE_INVALID", "借贷方向无效")
        sign = x.get("effect_sign", 1)
        if sign != 1 and not (reversal and sign == -1):
            raise AccountingError("SIGN_INVALID", "只有冲销凭证允许红字")
        cf = x.get("cashflow", "")
        if cf not in {"", "operating", "investing", "financing", "internal"}:
            raise AccountingError("CASHFLOW_INVALID", "现金流项目无效")
        amount_minor = minor(x["amount"])
        dimensions = await line_dimensions(
            session, book_id, a, x, amount_minor, kind="reversal" if reversal else kind
        )
        result.append(
            dict(
                **dimensions,
                summary=x.get("summary", ""),
                account_id=a.id,
                side=side,
                amount_minor=amount_minor,
                effect_sign=sign,
                auxiliary=x.get("auxiliary", ""),
                cashflow=cf,
            )
        )
    balanced(result)
    return result


async def create_voucher(session, book_id, data, *, kind="ordinary", reverses_id=None):
    await require(session, "create")
    book = await get_book(session, book_id)
    if not data["summary"].strip():
        raise AccountingError("SUMMARY_REQUIRED", "请填写凭证摘要")
    day = date.fromisoformat(str(data["accounting_date"]))
    await period(session, book_id, day)
    if kind == "opening":
        await require(session, "admin")
        if day != book.starts_on or book.opening_finalized:
            raise AccountingError(
                "OPENING_LOCKED", "期初日期须为启用日且只能核定一次", 409
            )
        if (
            await session.exec(
                select(Voucher).where(
                    Voucher.book_id == book_id,
                    Voucher.kind == "opening",
                    Voucher.state != "void",
                )
            )
        ).first():
            raise AccountingError("OPENING_EXISTS", "已有期初凭证，请在草稿中修改", 409)
    items = await validate_lines(
        session, book_id, data["lines"], reversal=kind == "reversal", kind=kind
    )
    if kind == "opening":
        mapping = {a.id: a for a in await accounts(session, book_id)}
        if any(mapping[x["account_id"]].kind in {"income", "expense"} for x in items):
            raise AccountingError(
                "OPENING_PROFIT", "期初不得直接记入本期损益，请将历史累计另行核对"
            )
    v = Voucher(
        book_id=book_id,
        accounting_date=day,
        summary=data["summary"],
        kind=kind,
        created_by=current_actor.get().snapshot(),
        reverses_id=reverses_id,
        reason=data.get("reason", ""),
    )
    session.add(v)
    await session.flush()
    for item in items:
        session.add(VoucherLine(voucher_id=v.id, **item))
    event(session, "create_voucher", book_id=book_id, voucher_id=v.id, kind=kind)
    await session.flush()
    return await detail(session, v.id, book_id)


async def get_voucher(session, book_id, voucher_id, version=None):
    v = await session.get(Voucher, voucher_id)
    if v is None or v.book_id != book_id:
        raise AccountingError("VOUCHER_NOT_FOUND", "凭证不存在", 404)
    if version is not None and version != v.version:
        raise AccountingError("VERSION_CONFLICT", "凭证已发生变化，请刷新", 409)
    return v


async def update_voucher(session, book_id, voucher_id, data):
    await require(session, "update")
    if not data["summary"].strip():
        raise AccountingError("SUMMARY_REQUIRED", "请填写凭证摘要")
    v = await get_voucher(session, book_id, voucher_id, data["expected_version"])
    if v.state != "draft" or v.kind not in {"ordinary", "opening"}:
        raise AccountingError(
            "IMMUTABLE", "仅普通或期初草稿可编辑；冲销与结转内容由系统核对生成", 409
        )
    await period(session, book_id, v.accounting_date)
    day = date.fromisoformat(str(data["accounting_date"]))
    await period(session, book_id, day)
    book = await get_book(session, book_id)
    if v.kind == "opening" and day != book.starts_on:
        raise AccountingError("OPENING_DATE", "期初日期须为启用日")
    items = await validate_lines(session, book_id, data["lines"], kind=v.kind)
    if v.kind == "opening":
        mapping = {a.id: a for a in await accounts(session, book_id)}
        if any(mapping[x["account_id"]].kind in {"income", "expense"} for x in items):
            raise AccountingError("OPENING_PROFIT", "期初不能记入损益科目")
    before = await detail(session, v.id, book_id)
    await session.exec(delete(VoucherLine).where(VoucherLine.voucher_id == v.id))
    for item in items:
        session.add(VoucherLine(voucher_id=v.id, **item))
    v.accounting_date, v.summary, v.version = day, data["summary"], v.version + 1
    session.add(v)
    event(
        session,
        "update_voucher",
        book_id=book_id,
        voucher_id=v.id,
        version=v.version,
        before=before,
        after=data,
    )
    await session.flush()
    return await detail(session, v.id, book_id)


async def action(session, book_id, voucher_id, action_name, version, reason=""):
    admin_actions = {"approve", "reject", "post", "revoke-approval"}
    await require(
        session,
        "admin"
        if action_name in admin_actions
        else "delete"
        if action_name == "void"
        else "update",
    )
    v = await get_voucher(session, book_id, voucher_id, version)
    p = await period(session, book_id, v.accounting_date)
    transitions = {
        "submit": ("draft", "submitted"),
        "withdraw": ("submitted", "draft"),
        "approve": ("submitted", "approved"),
        "reject": ("submitted", "draft"),
        "revoke-approval": ("approved", "draft"),
        "post": ("approved", "posted"),
    }
    if action_name == "void" and v.state in {"draft", "submitted", "approved"}:
        target = "void"
    elif action_name in transitions and v.state == transitions[action_name][0]:
        target = transitions[action_name][1]
    else:
        raise AccountingError("STATE_CONFLICT", "该凭证状态不允许此操作", 409)
    rows = await lines_for(session, v.id)
    balanced([x.model_dump() for x in rows])
    mapping = {a.id: a for a in await accounts(session, book_id)}
    if any(
        not mapping.get(x.account_id) or not mapping[x.account_id].enabled for x in rows
    ):
        raise AccountingError("ACCOUNT_INVALID", "科目已停用或不存在")
    if action_name in {"submit", "approve", "post"}:
        await validate_lines(
            session,
            book_id,
            [{**x.model_dump(), "amount": money(x.amount_minor)} for x in rows],
            reversal=v.kind == "reversal",
            kind=v.kind,
        )
    actor = current_actor.get().snapshot()
    # The user explicitly selected a single administrator. That administrator is the
    # sole reviewer/poster; self-review is recorded, never hidden behind a fake actor.
    if action_name == "approve":
        v.approved_by = actor
    if action_name == "post":
        if v.kind in {"closing", "annual_closing"}:
            from .closing import validate_closing

            await validate_closing(session, v, rows)
        book = await get_book(session, book_id)
        if not book.opening_finalized and v.kind != "opening":
            raise AccountingError("OPENING_REQUIRED", "请先核定期初余额", 409)
        if v.kind == "opening":
            book.opening_finalized = True
            session.add(book)
        v.number = f"{p.month}-记-{p.next_number:04d}"
        p.next_number += 1
        v.posted_by = actor
        session.add(p)
    if target == "draft":
        v.approved_by = {}
    v.state = target
    v.version += 1
    session.add(v)
    event(
        session,
        action_name,
        book_id=book_id,
        voucher_id=v.id,
        reason=reason,
        version=v.version,
    )
    await session.flush()
    return await detail(session, v.id, book_id)


async def reversal(session, book_id, voucher_id, day, reason):
    await require(session, "delete")
    v = await get_voucher(session, book_id, voucher_id)
    if v.state != "posted" or v.kind != "ordinary":
        raise AccountingError("REVERSAL_INVALID", "仅可冲销已过账的普通凭证", 409)
    if not reason.strip():
        raise AccountingError("REASON_REQUIRED", "请填写冲销原因")
    existing = (
        await session.exec(select(Voucher).where(Voucher.reverses_id == v.id))
    ).first()
    if existing:
        raise AccountingError("ALREADY_REVERSED", "冲销凭证已经存在", 409)
    rows = await lines_for(session, v.id)
    data = {
        "accounting_date": str(day),
        "summary": "冲销 " + v.number,
        "reason": reason,
        "lines": [
            {
                **x.model_dump(),
                "amount": money(x.amount_minor),
                "effect_sign": -x.effect_sign,
            }
            for x in rows
        ],
    }
    # A delete grant authorizes requesting reversal, not posting it.
    # Internal creation must not require an unrelated create grant.
    return await _reversal_create(session, book_id, data, v.id)


async def _reversal_create(session, book_id, data, original):
    day = date.fromisoformat(data["accounting_date"])
    await period(session, book_id, day)
    items = await validate_lines(session, book_id, data["lines"], reversal=True)
    v = Voucher(
        book_id=book_id,
        accounting_date=day,
        summary=data["summary"],
        reason=data["reason"],
        kind="reversal",
        reverses_id=original,
        created_by=current_actor.get().snapshot(),
    )
    session.add(v)
    await session.flush()
    for x in items:
        session.add(VoucherLine(voucher_id=v.id, **x))
    event(
        session,
        "reversal_draft",
        book_id=book_id,
        voucher_id=v.id,
        original=original,
        reason=data["reason"],
    )
    await session.flush()
    return await detail(session, v.id, book_id)


async def trial_balance(session, book_id, as_of=None):
    await get_book(session, book_id)
    chart = await accounts(session, book_id)
    stmt = (
        select(VoucherLine, Voucher)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.book_id == book_id, Voucher.state == "posted")
    )
    if as_of:
        stmt = stmt.where(Voucher.accounting_date <= as_of)
    records = (await session.exec(stmt)).all()
    from decimal import Decimal
    from .dimensions import plain

    quantities = {a.id: Decimal(0) for a in chart}
    totals = {a.id: {"debit": 0, "credit": 0} for a in chart}
    for line, v in records:
        totals[line.account_id][line.side] += line.amount_minor * line.effect_sign
        if line.quantity is not None:
            quantities[line.account_id] += (
                Decimal(line.quantity)
                * line.effect_sign
                * (1 if line.side == "debit" else -1)
            )
    rows = []
    for a in chart:
        values = totals[a.id]
        balance = values["debit"] - values["credit"]
        rows.append(
            {
                **a.model_dump(),
                "debit": money(values["debit"]),
                "credit": money(values["credit"]),
                "balance": money(balance),
                "balance_minor": balance,
                "quantity_balance": plain(quantities[a.id]),
            }
        )
    debit = sum(x["debit"] for x in totals.values())
    credit = sum(x["credit"] for x in totals.values())
    return {
        "rows": rows,
        "debit": money(debit),
        "credit": money(credit),
        "balanced": debit == credit,
    }


async def reports(session, book_id, start, end):
    book = await get_book(session, book_id)
    if end < start:
        raise AccountingError("DATE_RANGE", "日期范围无效")
    trial = await trial_balance(session, book_id, end)
    rows = trial["rows"]
    asset = sum(x["balance_minor"] for x in rows if x["kind"] == "asset")
    liability = -sum(x["balance_minor"] for x in rows if x["kind"] == "liability")
    equity = -sum(x["balance_minor"] for x in rows if x["kind"] == "equity")
    accumulated = -sum(
        x["balance_minor"] for x in rows if x["kind"] in {"income", "expense"}
    )
    equity += accumulated
    chart = {a.id: a for a in await accounts(session, book_id)}
    statement = (
        select(VoucherLine, Voucher)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(
            Voucher.book_id == book_id,
            Voucher.state == "posted",
            Voucher.kind != "opening",
            Voucher.accounting_date >= start,
            Voucher.accounting_date <= end,
        )
    )
    revenue = expense = 0
    cashflows = dict(operating=0, investing=0, financing=0)
    missing = []
    for line, v in (await session.exec(statement)).all():
        a = chart[line.account_id]
        delta = (
            line.amount_minor * line.effect_sign * (1 if line.side == "debit" else -1)
        )
        if v.kind != "closing":
            if a.kind == "income":
                revenue -= delta
            if a.kind == "expense":
                expense += delta
        if a.cash:
            if line.cashflow in cashflows:
                cashflows[line.cashflow] += delta
            elif line.cashflow != "internal":
                missing.append(v.id)
    # Internal cash movements must net to zero per voucher. Otherwise they cannot
    # disappear from the cash flow statement by being mislabeled.
    all_records = (await session.exec(statement)).all()
    internal = {}
    cash_change = 0
    for line, v in all_records:
        if chart[line.account_id].cash:
            delta = (
                line.amount_minor
                * line.effect_sign
                * (1 if line.side == "debit" else -1)
            )
            cash_change += delta
            if line.cashflow == "internal":
                internal[v.id] = internal.get(v.id, 0) + delta
    missing.extend(k for k, v in internal.items() if v)
    complete = not missing and sum(cashflows.values()) == cash_change
    event(session, "read_reports", book_id=book_id, start=str(start), end=str(end))
    return {
        "book": book.name,
        "start": str(start),
        "end": str(end),
        "opening_finalized": book.opening_finalized,
        "balance_sheet": {
            "assets": money(asset),
            "liabilities": money(liability),
            "equity": money(equity),
            "balanced": asset == liability + equity,
        },
        "income_statement": {
            "income": money(revenue),
            "expense": money(expense),
            "profit": money(revenue - expense),
        },
        "cash_flow": {
            "complete": complete,
            "items": {k: money(v) for k, v in cashflows.items()} if complete else None,
            "cash_change": money(cash_change),
            "unclassified_voucher_ids": sorted(set(missing)),
        },
        "notice": "核算内测；年中启用前累计数据不在本报表覆盖范围，不作为完整年度报表",
    }


async def pre_close(session, book_id, month):
    from .closing import close_checks

    return await close_checks(session, book_id, month)


async def idempotent(session, key, payload, operation):
    if not key or not 1 <= len(key) <= 160:
        raise AccountingError(
            "IDEMPOTENCY_REQUIRED", "需要 1–160 字符的 Idempotency-Key"
        )
    actor = current_actor.get().key
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    previous = (
        await session.exec(
            select(CommandReceipt).where(
                CommandReceipt.actor_key == actor, CommandReceipt.key == key
            )
        )
    ).first()
    if previous:
        if previous.fingerprint != digest:
            raise AccountingError(
                "IDEMPOTENCY_CONFLICT", "同一请求标识不能用于不同内容", 409
            )
        return previous.result
    result = await operation()
    session.add(
        CommandReceipt(actor_key=actor, key=key, fingerprint=digest, result=result)
    )
    await session.flush()
    return result


async def active_book(session):
    from .models import AccessControl

    control = await session.get(AccessControl, 1)
    if control and control.active_book_id:
        chosen = await session.get(Book, control.active_book_id)
        if chosen and not chosen.archived:
            return chosen
    return (
        await session.exec(select(Book).where(Book.archived == False).order_by(Book.id))
    ).first()


async def has_books(session):
    return (await session.exec(select(Book.id).limit(1))).first() is not None


async def writable_book(session, book_id):
    book = await get_book(session, book_id)
    if book.archived:
        raise AccountingError("BOOK_ARCHIVED", "账套已归档，请先恢复后再操作", 409)
    return book


async def business_draft(session, book_id, source_key, facts):
    await require(session, "create")
    await writable_book(session, book_id)
    item = (
        await session.exec(
            select(BusinessDraft).where(
                BusinessDraft.book_id == book_id, BusinessDraft.source_key == source_key
            )
        )
    ).first()
    if item:
        if item.facts != facts:
            raise AccountingError("SOURCE_CONFLICT", "来源标识已用于其他内容", 409)
        return item
    item = BusinessDraft(
        book_id=book_id,
        source_key=source_key,
        facts=facts,
        actor=current_actor.get().snapshot(),
    )
    session.add(item)
    await session.flush()
    event(
        session,
        "create_business_draft",
        book_id=book_id,
        draft_id=item.id,
        source_key=source_key,
    )
    return item
