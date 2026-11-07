"""Month-end close with explicit cash reconciliation and profit carry-forward."""

import calendar
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlmodel import select

from . import service as svc
from .domain import AccountingError, money
from .identity import require
from .models import BusinessDraft, Period, Reconciliation, Voucher, VoucherLine


def month_end(month):
    try:
        day = date.fromisoformat(month + "-01")
        if day.strftime("%Y-%m") != month:
            raise ValueError
    except (TypeError, ValueError):
        raise AccountingError("PERIOD_INVALID", "期间格式应为 YYYY-MM") from None
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def signed_minor(value):
    """Validate a signed decimal balance without accepting binary floats."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise AccountingError("INVALID_MONEY", "对账金额必须使用十进制字符串")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) > Decimal("100000000"):
            raise ValueError
        if amount != amount.quantize(Decimal(".01")):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        raise AccountingError(
            "INVALID_MONEY", "对账金额无效，最多两位小数且不超过一亿元"
        ) from None
    return int(amount * 100)


async def ledger_fingerprint(session, book_id, account_id, as_of):
    """Invalidate reconciliation even when later posted movements net to zero."""
    rows = (
        await session.exec(
            select(VoucherLine, Voucher)
            .join(Voucher, Voucher.id == VoucherLine.voucher_id)
            .where(
                Voucher.book_id == book_id,
                Voucher.state == "posted",
                VoucherLine.account_id == account_id,
                Voucher.accounting_date <= as_of,
            )
            .order_by(VoucherLine.id)
        )
    ).all()
    values = [
        [
            line.id,
            v.id,
            str(v.accounting_date),
            line.side,
            line.amount_minor,
            line.effect_sign,
        ]
        for line, v in rows
    ]
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode()
    ).hexdigest()


async def reconciliation(
    session, book_id, account_id, as_of, statement_balance, items, reason
):
    await require(session, "admin")
    await svc.period(session, book_id, as_of)
    chart = {a.id: a for a in await svc.accounts(session, book_id)}
    if account_id not in chart or not chart[account_id].cash:
        raise AccountingError("CASH_ACCOUNT_REQUIRED", "对账科目必须为本账套资金科目")
    actual = signed_minor(statement_balance)
    adjustments = 0
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"side", "amount", "reason"}:
            raise AccountingError(
                "OUTSTANDING_INVALID", "未达项须包含 side、amount、reason"
            )
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise AccountingError("REASON_REQUIRED", "每条未达项需要说明")
        if item["side"] not in {"bank", "book"}:
            raise AccountingError("SIDE_INVALID", "未达项归属须为 bank 或 book")
        amount = signed_minor(item["amount"])
        adjustments += amount if item["side"] == "bank" else -amount
        normalized.append({**item, "amount_minor": amount})
    balances = await svc.trial_balance(session, book_id, as_of)
    ledger = next(r["balance_minor"] for r in balances["rows"] if r["id"] == account_id)
    if actual + adjustments != ledger:
        raise AccountingError(
            "RECONCILIATION_DIFFERENCE", "银行余额与账内余额经未达项调整后仍不一致"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise AccountingError("REASON_REQUIRED", "请填写对账说明和依据")
    row = Reconciliation(
        book_id=book_id,
        account_id=account_id,
        as_of=as_of,
        statement_minor=actual,
        ledger_minor=ledger,
        items=normalized,
        reason=reason,
        ledger_fingerprint=await ledger_fingerprint(
            session, book_id, account_id, as_of
        ),
    )
    session.add(row)
    await session.flush()
    svc.event(
        session,
        "reconcile_cash_account",
        book_id=book_id,
        reconciliation=row.model_dump(mode="json"),
    )
    return row.model_dump(mode="json")


async def close_checks(session, book_id, month):
    book = await svc.get_book(session, book_id)
    end = month_end(month)
    if end < book.starts_on:
        raise AccountingError("BEFORE_START", "期间早于启用日")
    period = (
        await session.exec(
            select(Period).where(Period.book_id == book_id, Period.month == month)
        )
    ).first()
    if period and period.closed:
        return {
            "month": month,
            "can_close": False,
            "closed": True,
            "issues": ["期间已关闭"],
        }
    issues = []
    if not book.opening_finalized:
        issues.append("期初尚未核定")
    pending = (
        await session.exec(
            select(Voucher).where(
                Voucher.book_id == book_id,
                Voucher.state.notin_(["posted", "void"]),
                Voucher.accounting_date <= end,
            )
        )
    ).all()
    if pending:
        issues.append("本期及以前存在未过账凭证")
    business = (
        await session.exec(
            select(BusinessDraft).where(
                BusinessDraft.book_id == book_id, BusinessDraft.state == "pending"
            )
        )
    ).all()
    if business:
        issues.append("存在待制证业务草稿")
    cursor = book.starts_on.replace(day=1)
    closed = {
        p.month
        for p in (
            await session.exec(
                select(Period).where(Period.book_id == book_id, Period.closed == True)
            )
        ).all()
    }
    while cursor.strftime("%Y-%m") < month:
        if cursor.strftime("%Y-%m") not in closed:
            issues.append("前期尚未结账：" + cursor.strftime("%Y-%m"))
        cursor = date(
            cursor.year + (cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )
    trial = await svc.trial_balance(session, book_id, end)
    if not trial["balanced"]:
        issues.append("试算不平衡")
    if any(
        r["kind"] in {"income", "expense"} and r["balance_minor"] for r in trial["rows"]
    ):
        issues.append("损益科目尚未结转")
    if end.month == 12 and any(
        r["code"] == "3103" and r["balance_minor"] for r in trial["rows"]
    ):
        issues.append("年末本年利润尚未转入利润分配")
    for account in trial["rows"]:
        if not account["cash"]:
            continue
        rec = (
            await session.exec(
                select(Reconciliation)
                .where(
                    Reconciliation.book_id == book_id,
                    Reconciliation.account_id == account["id"],
                    Reconciliation.as_of == end,
                )
                .order_by(Reconciliation.id.desc())
            )
        ).first()
        signature = await ledger_fingerprint(session, book_id, account["id"], end)
        if (
            not rec
            or rec.ledger_minor != account["balance_minor"]
            or rec.ledger_fingerprint != signature
        ):
            issues.append(
                "资金科目需重新对账：" + account["code"] + " " + account["name"]
            )
    reports = await svc.reports(session, book_id, book.starts_on, end)
    if not reports["cash_flow"]["complete"]:
        issues.append("现金流量分类不完整或内部转账不平")
    return {"month": month, "can_close": not issues, "closed": False, "issues": issues}


async def closing_lines(session, book_id, month, annual=False):
    end = month_end(month)
    trial = await svc.trial_balance(session, book_id, end)
    rows = trial["rows"]
    chart = {r["code"]: r for r in rows}
    if annual:
        if end.month != 12:
            raise AccountingError("YEAR_END", "年度利润结转仅允许 12 月")
        if any(r["kind"] in {"income", "expense"} and r["balance_minor"] for r in rows):
            raise AccountingError(
                "MONTHLY_CLOSING_REQUIRED", "年度结转前须先完成月度损益结转", 409
            )
        selected, target = [chart["3103"]], chart["3104"]
    else:
        selected = [r for r in rows if r["kind"] in {"income", "expense"}]
        target = chart["3103"]
    entries = []
    total = 0
    for account in selected:
        balance = account["balance_minor"]
        if not balance:
            continue
        entries.append(
            dict(
                account_id=account["id"],
                side="credit" if balance > 0 else "debit",
                amount=money(abs(balance)),
            )
        )
        total += balance
    if entries and total:
        entries.append(
            dict(
                account_id=target["id"],
                side="debit" if total > 0 else "credit",
                amount=money(abs(total)),
            )
        )
    return entries


async def validate_closing(session, voucher, lines):
    """Posting rechecks the generated carry-forward against current posted balances."""
    month = voucher.accounting_date.strftime("%Y-%m")
    if voucher.accounting_date != month_end(month):
        raise AccountingError("CLOSING_DATE", "结转日期必须为月末")
    expected = await closing_lines(
        session, voucher.book_id, month, voucher.kind == "annual_closing"
    )
    actual = sorted(
        (line.account_id, line.side, line.amount_minor, line.effect_sign)
        for line in lines
    )
    normalized = sorted(
        (line["account_id"], line["side"], signed_minor(line["amount"]), 1)
        for line in expected
    )
    if actual != normalized:
        raise AccountingError(
            "CLOSING_STALE", "账务已变化，请作废当前结转草稿并重新生成", 409
        )


async def closing_draft(session, book_id, month, annual=False):
    await require(session, "admin")
    end = month_end(month)
    await svc.period(session, book_id, end)
    kind = "annual_closing" if annual else "closing"
    pending = (
        await session.exec(
            select(Voucher).where(
                Voucher.book_id == book_id,
                Voucher.kind == kind,
                Voucher.state.notin_(["posted", "void"]),
                Voucher.accounting_date == end,
            )
        )
    ).first()
    if pending:
        raise AccountingError(
            "CLOSING_EXISTS", f"已有结转草稿 #{pending.id}，请先处理", 409
        )
    entries = await closing_lines(session, book_id, month, annual)
    if not entries:
        return {"no_entry": True, "message": "无需结转"}
    return await svc.create_voucher(
        session,
        book_id,
        {
            "accounting_date": str(end),
            "summary": ("年度利润分配结转" if annual else "月度损益结转") + " " + month,
            "lines": entries,
        },
        kind=kind,
    )


async def close(session, book_id, month):
    await require(session, "admin")
    check = await close_checks(session, book_id, month)
    if not check["can_close"]:
        raise AccountingError("CLOSE_BLOCKED", "；".join(check["issues"]), 409)
    end = month_end(month)
    period = await svc.period(session, book_id, end)
    snapshot = await svc.reports(
        session, book_id, (await svc.get_book(session, book_id)).starts_on, end
    )
    period.closed = True
    session.add(period)
    svc.event(session, "close_period", book_id=book_id, month=month, snapshot=snapshot)
    await session.flush()
    return {"closed": True, "month": month, "snapshot": snapshot}
