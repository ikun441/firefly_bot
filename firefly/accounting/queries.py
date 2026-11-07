"""Shared voucher filtering for the Web API and QQ command."""

from sqlalchemy import func
from sqlmodel import select

from . import service as svc
from .domain import AccountingError
from .models import Voucher


async def list_vouchers(session, book_id, *, start=None, end=None, offset=0, limit=50):
    await svc.get_book(session, book_id)
    if start and end and end < start:
        raise AccountingError("DATE_RANGE", "截止日期不能早于开始日期")
    conditions = [Voucher.book_id == book_id]
    if start:
        conditions.append(Voucher.accounting_date >= start)
    if end:
        conditions.append(Voucher.accounting_date <= end)
    total = (
        await session.exec(select(func.count(Voucher.id)).where(*conditions))
    ).one()
    rows = (
        await session.exec(
            select(Voucher)
            .where(*conditions)
            .order_by(Voucher.accounting_date.desc(), Voucher.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    svc.event(
        session,
        "list_vouchers",
        book_id=book_id,
        start=str(start) if start else None,
        end=str(end) if end else None,
        offset=offset,
        limit=limit,
    )
    return {
        "rows": [v.model_dump(mode="json") for v in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }
