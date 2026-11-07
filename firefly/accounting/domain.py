"""Pure accounting invariants; no HTTP or ORM dependencies."""

from decimal import Decimal, InvalidOperation


class AccountingError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


def minor(value):
    if isinstance(value, (bool, float)):
        raise AccountingError("INVALID_MONEY", "金额必须使用十进制字符串")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AccountingError("INVALID_MONEY", "金额无效") from None
    if not number.is_finite() or number <= 0 or number > Decimal("100000000"):
        raise AccountingError("INVALID_MONEY", "金额须为不超过一亿元的正数")
    if number != number.quantize(Decimal("0.01")):
        raise AccountingError("INVALID_MONEY", "金额最多两位小数")
    return int(number * 100)


def money(value):
    return format(Decimal(value) / 100, ".2f")


def balanced(lines):
    if len(lines) < 2:
        raise AccountingError("UNBALANCED", "凭证至少需要两条分录")
    debit = sum(
        x["amount_minor"] * x.get("effect_sign", 1)
        for x in lines
        if x["side"] == "debit"
    )
    credit = sum(
        x["amount_minor"] * x.get("effect_sign", 1)
        for x in lines
        if x["side"] == "credit"
    )
    if debit != credit or not debit:
        raise AccountingError("UNBALANCED", "凭证借贷不平衡或合计为零")
