"""Expense categories and exact quantity-price validation for voucher lines."""

import re
from decimal import Decimal, ROUND_HALF_UP
from sqlmodel import select
from .domain import AccountingError
from .models import ExpenseCategory


def decimal_value(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d{1,6})?", value):
        raise AccountingError("QUANTITY_INVALID", label + "须为最多六位小数的正数")
    number = Decimal(value)
    if not 0 < number <= Decimal("1000000000"):
        raise AccountingError("QUANTITY_INVALID", label + "须大于零且不超过十亿")
    return number


def plain(number):
    value = format(number, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


async def line_dimensions(session, book_id, account, line, amount_minor, *, kind):
    category_id = line.get("budget_category_id")
    if category_id is not None:
        category = await session.get(ExpenseCategory, category_id)
        if (
            not category
            or category.book_id != book_id
            or (not category.enabled and kind != "reversal")
            or account.kind != "expense"
        ):
            raise AccountingError(
                "BUDGET_CATEGORY_INVALID",
                "费用类别不存在、已停用或不属于该账套的费用科目",
            )
    elif account.kind == "expense" and kind == "ordinary":
        configured = (
            await session.exec(
                select(ExpenseCategory).where(
                    ExpenseCategory.book_id == book_id, ExpenseCategory.enabled == True
                )
            )
        ).first()
        if configured:
            raise AccountingError(
                "BUDGET_CATEGORY_REQUIRED", "请为费用分录选择预算费用类别"
            )
    quantity, price = line.get("quantity"), line.get("unit_price")
    unit = line.get("unit", "") or account.unit
    if quantity in (None, "") and price in (None, ""):
        if account.quantity_enabled and kind not in {
            "reversal",
            "closing",
            "annual_closing",
        }:
            raise AccountingError(
                "QUANTITY_REQUIRED", "该科目已开启数量核算，请填写数量和单价"
            )
        return dict(
            budget_category_id=category_id, quantity=None, unit_price=None, unit=""
        )
    if not account.quantity_enabled and kind != "reversal":
        raise AccountingError("QUANTITY_DISABLED", "请先在科目管理中开启数量核算")
    q, p = decimal_value(quantity, "数量"), decimal_value(price, "单价")
    calculated = (q * p).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    if int(calculated * 100) != amount_minor:
        raise AccountingError(
            "QUANTITY_AMOUNT_MISMATCH",
            "数量乘单价按四舍五入保留两位后，必须等于分录金额",
        )
    if not isinstance(unit, str) or not unit.strip() or len(unit) > 16:
        raise AccountingError("UNIT_REQUIRED", "请填写不超过16字的计量单位")
    return dict(
        budget_category_id=category_id,
        quantity=plain(q),
        unit_price=plain(p),
        unit=unit.strip(),
    )
