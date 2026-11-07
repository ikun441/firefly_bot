"""Account hierarchy management with immutable historical classifications."""

from sqlmodel import select
from . import service as svc
from .domain import AccountingError
from .identity import require
from .models import ChartAccount, VoucherLine


async def create_account(session, book_id, data):
    await require(session, "admin")
    await svc.writable_book(session, book_id)
    existing = await svc.accounts(session, book_id)
    code = data["code"].strip()
    name = data["name"].strip()
    if (
        not code.isascii()
        or not code.isdigit()
        or not 4 <= len(code) <= 20
        or any(a.code == code for a in existing)
    ):
        raise AccountingError("ACCOUNT_INVALID", "科目编码须为唯一的4至20位数字")
    if not name:
        raise AccountingError("NAME_REQUIRED", "请填写科目名称")
    parent_id = data.get("parent_id")
    parent = (
        next((a for a in existing if a.id == parent_id), None) if parent_id else None
    )
    prefixes = [a for a in existing if code.startswith(a.code)]
    nearest = max(prefixes, key=lambda a: len(a.code), default=None)
    if parent_id and (not parent or not parent.enabled):
        raise AccountingError("PARENT_INVALID", "上级科目不存在、已停用或不属于本账套")
    if nearest and (not parent or parent.id != nearest.id):
        raise AccountingError(
            "PARENT_REQUIRED", "该编码属于现有科目的下级，请选择正确的上级科目"
        )
    if parent and (not code.startswith(parent.code) or len(code) <= len(parent.code)):
        raise AccountingError("CHILD_CODE_INVALID", "子科目编码须在上级编码后追加数字")
    if any(a.code.startswith(code) for a in existing):
        raise AccountingError("ACCOUNT_HIERARCHY", "不能插入新的上级改变已有科目层级")
    if parent and (data["kind"], data["normal_side"], data["cash"]) != (
        parent.kind,
        parent.normal_side,
        parent.cash,
    ):
        raise AccountingError(
            "PARENT_TYPE", "子科目须继承上级的科目类别、余额方向和资金属性"
        )
    if data["kind"] not in {
        "asset",
        "liability",
        "equity",
        "income",
        "expense",
    } or data["normal_side"] not in {"debit", "credit"}:
        raise AccountingError("ACCOUNT_INVALID", "科目类别或方向无效")
    unit = data.get("unit", "件").strip()
    if not unit or len(unit) > 16:
        raise AccountingError("UNIT_REQUIRED", "计量单位须为1至16字")
    row = ChartAccount(
        book_id=book_id, **{**data, "code": code, "name": name, "unit": unit}
    )
    session.add(row)
    await session.flush()
    svc.event(
        session,
        "create_account",
        book_id=book_id,
        code=code,
        name=name,
        parent_name=parent.name if parent else "",
        quantity_enabled=row.quantity_enabled,
        unit=unit,
    )
    return row.model_dump()


async def update_account(session, book_id, account_id, data):
    await require(session, "admin")
    await svc.writable_book(session, book_id)
    row = await session.get(ChartAccount, account_id)
    if not row or row.book_id != book_id:
        raise AccountingError("ACCOUNT_NOT_FOUND", "科目不存在", 404)
    if row.version != data["expected_version"]:
        raise AccountingError("VERSION_CONFLICT", "科目已变化，请刷新", 409)
    name, unit = data["name"].strip(), data["unit"].strip()
    if not name or not unit:
        raise AccountingError("NAME_REQUIRED", "科目名称和计量单位不能为空")
    if unit != row.unit:
        history = (
            await session.exec(
                select(VoucherLine).where(
                    VoucherLine.account_id == row.id, VoucherLine.quantity != None
                )
            )
        ).first()
        if history:
            raise AccountingError(
                "UNIT_LOCKED", "已有数量记录，不能改变计量单位；请另建子科目", 409
            )
    before = row.model_dump()
    for field in ["enabled", "quantity_enabled"]:
        setattr(row, field, data[field])
    row.name, row.unit, row.version = name, unit, row.version + 1
    session.add(row)
    await session.flush()
    svc.event(
        session,
        "update_account",
        book_id=book_id,
        code=row.code,
        name=name,
        before=before,
        enabled=row.enabled,
        quantity_enabled=row.quantity_enabled,
        unit=unit,
    )
    return row.model_dump()


async def chart(session, book_id):
    await svc.get_book(session, book_id)
    rows = await svc.accounts(session, book_id)
    parents = {a.parent_id for a in rows if a.parent_id}
    return [{**a.model_dump(), "is_leaf": a.id not in parents} for a in rows]
