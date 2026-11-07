"""Multiple accounting books; deletion archives records without erasing evidence."""

from sqlmodel import select
from sqlalchemy import func
from . import service as svc
from .domain import AccountingError
from .identity import require
from .models import Book, AccessControl, Voucher


async def list_books(session, include_archived=False):
    rows = (await session.exec(select(Book).order_by(Book.id))).all()
    control = await session.get(AccessControl, 1)
    return [
        {
            **b.model_dump(mode="json"),
            "is_current": bool(control and control.active_book_id == b.id),
        }
        for b in rows
        if include_archived or not b.archived
    ]


async def detail(session, book_id):
    book = await svc.get_book(session, book_id)
    total = (
        await session.exec(
            select(func.count(Voucher.id)).where(Voucher.book_id == book_id)
        )
    ).one()
    posted = (
        await session.exec(
            select(func.count(Voucher.id)).where(
                Voucher.book_id == book_id, Voucher.state == "posted"
            )
        )
    ).one()
    return {
        **book.model_dump(mode="json"),
        "voucher_count": total,
        "posted_count": posted,
    }


async def update(session, book_id, name, starts_on, version, archived=None):
    await require(session, "admin")
    book = await svc.get_book(session, book_id)
    if book.version != version:
        raise AccountingError("VERSION_CONFLICT", "账套已变化，请刷新", 409)
    name = name.strip()
    if not name:
        raise AccountingError("NAME_REQUIRED", "请输入账套名称")
    if starts_on.day != 1:
        raise AccountingError("START_DATE", "启用日期须为月初")
    if starts_on != book.starts_on:
        voucher = (
            await session.exec(select(Voucher).where(Voucher.book_id == book_id))
        ).first()
        if voucher or book.opening_finalized:
            raise AccountingError(
                "BOOK_DATE_LOCKED", "已有凭证或已核定期初，不能改变启用日期", 409
            )
    before = book.model_dump(mode="json")
    book.name, book.starts_on, book.version = name, starts_on, book.version + 1
    if archived is not None:
        book.archived = archived
    session.add(book)
    await session.flush()
    svc.event(
        session,
        "update_book",
        book_id=book_id,
        name=name,
        before=before,
        archived=book.archived,
    )
    return await detail(session, book_id)


async def activate(session, book_id):
    await require(session, "admin")
    book = await svc.get_book(session, book_id)
    if book.archived:
        raise AccountingError("BOOK_ARCHIVED", "请先恢复该账套", 409)
    control = await session.get(AccessControl, 1)
    before = control.active_book_id
    control.active_book_id = book.id
    session.add(control)
    svc.event(
        session, "activate_book", book_id=book.id, name=book.name, previous_book=before
    )
    await session.flush()
    return {"active_book_id": book.id, "name": book.name}


async def archive(session, book_id):
    await require(session, "admin")
    book = await svc.get_book(session, book_id)
    book.archived = True
    book.version += 1
    session.add(book)
    control = await session.get(AccessControl, 1)
    if control.active_book_id == book.id:
        other = (
            await session.exec(
                select(Book)
                .where(Book.id != book.id, Book.archived == False)
                .order_by(Book.id)
            )
        ).first()
        control.active_book_id = other.id if other else None
        session.add(control)
    svc.event(session, "archive_book", book_id=book.id, name=book.name)
    await session.flush()
    return {"archived": True, "message": "账套已归档，凭证与操作记录完整保留，可恢复"}
