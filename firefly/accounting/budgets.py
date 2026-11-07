"""Monthly expense budgets based only on posted expense voucher lines."""

from datetime import date
from decimal import Decimal
from sqlmodel import select
from . import service as svc
from .closing import month_end
from .domain import AccountingError, minor, money
from .identity import require
from .models import ExpenseCategory, ExpenseBudget, Voucher, VoucherLine, ChartAccount


async def categories(session, book_id):
    await svc.get_book(session, book_id)
    return list(
        (
            await session.exec(
                select(ExpenseCategory)
                .where(ExpenseCategory.book_id == book_id)
                .order_by(ExpenseCategory.id)
            )
        ).all()
    )


async def save_category(
    session, book_id, name, category_id=None, enabled=True, expected_version=None
):
    await require(session, "admin")
    await svc.writable_book(session, book_id)
    name = name.strip()
    if not name:
        raise AccountingError("NAME_REQUIRED", "请填写费用类别名称")
    rows = await categories(session, book_id)
    if any(c.name == name and c.id != category_id for c in rows):
        raise AccountingError("CATEGORY_EXISTS", "该费用类别已存在", 409)
    category = await session.get(ExpenseCategory, category_id) if category_id else None
    if category_id and (not category or category.book_id != book_id):
        raise AccountingError("CATEGORY_NOT_FOUND", "费用类别不存在", 404)
    if (
        category
        and expected_version is not None
        and category.version != expected_version
    ):
        raise AccountingError("VERSION_CONFLICT", "类别已变化，请刷新", 409)
    before = category.model_dump() if category else None
    if category is None:
        category = ExpenseCategory(book_id=book_id, name=name)
    category.name, category.enabled = name, enabled
    category.version += 1
    session.add(category)
    await session.flush()
    svc.event(
        session,
        "save_expense_category",
        book_id=book_id,
        category_name=name,
        before=before,
        enabled=enabled,
        category_id=category.id,
    )
    return category.model_dump()


async def set_budget(session, book_id, category_id, month, amount):
    await require(session, "admin")
    await svc.writable_book(session, book_id)
    month_end(month)
    category = await session.get(ExpenseCategory, category_id)
    if not category or category.book_id != book_id or not category.enabled:
        raise AccountingError("CATEGORY_NOT_FOUND", "请选择本账套启用的费用类别")
    value = minor(amount)
    budget = (
        await session.exec(
            select(ExpenseBudget).where(
                ExpenseBudget.book_id == book_id,
                ExpenseBudget.category_id == category_id,
                ExpenseBudget.month == month,
            )
        )
    ).first()
    before = budget.amount_minor if budget else None
    if not budget:
        budget = ExpenseBudget(
            book_id=book_id, category_id=category_id, month=month, amount_minor=value
        )
    budget.amount_minor = value
    session.add(budget)
    await session.flush()
    svc.event(
        session,
        "set_expense_budget",
        book_id=book_id,
        category_name=category.name,
        month=month,
        amount=money(value),
        previous_amount=money(before) if before is not None else None,
    )
    return budget.model_dump()


async def overview(session, book_id, month):
    month_last = month_end(month)
    start = date.fromisoformat(month + "-01")
    cats = await categories(session, book_id)
    plans = (
        await session.exec(
            select(ExpenseBudget).where(
                ExpenseBudget.book_id == book_id, ExpenseBudget.month == month
            )
        )
    ).all()
    plan = {b.category_id: b for b in plans}
    records = (
        await session.exec(
            select(VoucherLine, ChartAccount)
            .join(Voucher, Voucher.id == VoucherLine.voucher_id)
            .join(ChartAccount, ChartAccount.id == VoucherLine.account_id)
            .where(
                Voucher.book_id == book_id,
                Voucher.state == "posted",
                Voucher.kind.in_(["ordinary", "reversal"]),
                Voucher.accounting_date >= start,
                Voucher.accounting_date <= month_last,
                ChartAccount.kind == "expense",
            )
        )
    ).all()
    used = {}
    for line, account in records:
        delta = (
            line.amount_minor * line.effect_sign * (1 if line.side == "debit" else -1)
        )
        used[line.budget_category_id] = used.get(line.budget_category_id, 0) + delta
    result = []
    for category in cats:
        amount = plan[category.id].amount_minor if category.id in plan else None
        actual = used.get(category.id, 0)
        ratio = plain_ratio(actual, amount) if amount else None
        result.append(
            dict(
                **category.model_dump(),
                budget=money(amount) if amount is not None else None,
                used=money(actual),
                remaining=money(amount - actual) if amount is not None else None,
                ratio=ratio,
                state="未设预算"
                if amount is None
                else "已超预算"
                if actual > amount
                else "接近额度"
                if Decimal(actual) >= Decimal(amount) * Decimal(".8")
                else "额度充足",
            )
        )
    svc.event(session, "read_expense_budgets", book_id=book_id, month=month)
    return {
        "month": month,
        "categories": result,
        "planned": money(sum(b.amount_minor for b in plans)),
        "used": money(sum(used.values())),
        "unassigned": money(used.get(None, 0)),
        "basis": "已过账费用分录，冲销抵减，损益结转不重复计入",
    }


def plain_ratio(actual, amount):
    return str((Decimal(actual) * 100 / Decimal(amount)).quantize(Decimal(".1")))
