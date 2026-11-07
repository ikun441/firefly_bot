"""v2 accounting API. Web identities are established by AuthMiddleware."""

from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, UploadFile, File
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from ..db.session import get_engine
from . import service as svc
from .domain import AccountingError
from .identity import access, current_actor, qq_number, require
from .models import (
    AccessControl,
    QQGrant,
    Voucher,
    AccountingEvent,
    BusinessDraft,
)

router = APIRouter(prefix="/v2", tags=["accounting"])


@asynccontextmanager
async def transaction():
    # All v2 commands serialize before reading, across processes and connections.
    # Do not rely on process-local locks or SELECT FOR UPDATE on SQLite.
    async with AsyncSession(get_engine(), expire_on_commit=False) as session:
        if session.bind.dialect.name != "sqlite":
            raise AccountingError(
                "DATABASE_UNSUPPORTED", "本版本仅支持已验证的 SQLite 部署", 503
            )
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def db():
    async with transaction() as session:
        yield session


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantIn(Strict):
    nickname: str = Field(default="", max_length=64)
    enabled: bool = True
    can_create: bool = False
    can_read: bool = False
    can_update: bool = False
    can_delete: bool = False


class AdminBinding(Strict):
    admin_qq: str = ""
    expected_version: int


@router.get("/access")
async def my_access(s=Depends(db)):
    return {"success": True, "data": await access(s)}


@router.get("/permissions")
async def permissions(s=Depends(db)):
    await require(s, "admin")
    control = await s.get(AccessControl, 1)
    return {
        "success": True,
        "data": {
            "administrator": current_actor.get().identifier,
            "admin_qq": control.admin_qq,
            "version": control.version,
            "grants": [
                g.model_dump()
                for g in (await s.exec(select(QQGrant).order_by(QQGrant.qq))).all()
            ],
        },
    }


@router.put("/permissions/administrator")
async def bind_admin(payload: AdminBinding, s=Depends(db)):
    await require(s, "admin")
    qq = qq_number(payload.admin_qq) if payload.admin_qq else ""
    control = await s.get(AccessControl, 1)
    if control.version != payload.expected_version:
        raise AccountingError("VERSION_CONFLICT", "管理员绑定已变化，请刷新", 409)
    old = control.admin_qq
    control.admin_qq = qq
    control.version += 1
    s.add(control)
    svc.event(s, "bind_administrator", before=old, after=qq)
    return {"success": True, "data": control.model_dump()}


@router.put("/permissions/qq/{qq}")
async def save_grant(qq: str, payload: GrantIn, s=Depends(db)):
    await require(s, "admin")
    qq = qq_number(qq)
    control = await s.get(AccessControl, 1)
    if control.admin_qq == qq:
        raise AccountingError(
            "ADMIN_IMMUTABLE", "管理员 QQ 是唯一管理员的绑定身份，不能作为普通账号授权"
        )
    g = (await s.exec(select(QQGrant).where(QQGrant.qq == qq))).first()
    before = g.model_dump() if g else None
    if g is None:
        g = QQGrant(qq=qq)
    for k, v in payload.model_dump().items():
        setattr(g, k, v)
    g.version += 1
    s.add(g)
    svc.event(s, "set_qq_permissions", qq=qq, before=before, after=payload.model_dump())
    await s.flush()
    return {"success": True, "data": g.model_dump()}


@router.delete("/permissions/qq/{qq}")
async def revoke_grant(qq: str, s=Depends(db)):
    await require(s, "admin")
    g = (await s.exec(select(QQGrant).where(QQGrant.qq == qq_number(qq)))).first()
    if g:
        g.enabled = False
        g.can_create = g.can_read = g.can_update = g.can_delete = False
        g.version += 1
        s.add(g)
    svc.event(s, "revoke_qq_permissions", qq=qq)
    return {"success": True, "data": {"revoked": True}}


class BookIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    starts_on: date


@router.get("/books")
async def books(include_archived: bool = False, s=Depends(db)):
    from .books import list_books

    await require(s, "read")
    svc.event(s, "list_books")
    return {"success": True, "data": await list_books(s, include_archived)}


@router.post("/books")
async def new_book(
    p: BookIn, s=Depends(db), key: str = Header(default="", alias="Idempotency-Key")
):
    result = await svc.idempotent(
        s,
        key,
        {"action": "book", **p.model_dump(mode="json")},
        lambda: svc.create_book(s, p.name, p.starts_on),
    )
    return {"success": True, "data": result}


class LineIn(Strict):
    account_id: int
    side: Literal["debit", "credit"]
    amount: str
    summary: str = Field(default="", max_length=512)
    auxiliary: str = Field(default="", max_length=128)
    cashflow: Literal["", "operating", "investing", "financing", "internal"] = ""
    budget_category_id: int | None = None
    quantity: str | None = Field(default=None, max_length=32)
    unit_price: str | None = Field(default=None, max_length=32)
    unit: str = Field(default="", max_length=16)


class VoucherIn(Strict):
    accounting_date: date
    summary: str = Field(min_length=1, max_length=512)
    lines: list[LineIn] = Field(min_length=2, max_length=200)
    opening: bool = False


class VoucherPatch(Strict):
    accounting_date: date
    summary: str = Field(min_length=1, max_length=512)
    lines: list[LineIn] = Field(min_length=2, max_length=200)
    expected_version: int


class ActionIn(Strict):
    expected_version: int
    reason: str = Field(default="", max_length=512)


class ReverseIn(Strict):
    accounting_date: date
    reason: str = Field(min_length=1, max_length=512)


class AccountIn(Strict):
    code: str = Field(min_length=4, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["asset", "liability", "equity", "income", "expense"]
    normal_side: Literal["debit", "credit"]
    cash: bool = False
    enabled: bool = True
    parent_id: int | None = None
    quantity_enabled: bool = False
    unit: str = Field(default="件", min_length=1, max_length=16)


@router.get("/books/{book_id}/chart-accounts")
async def chart(book_id: int, s=Depends(db)):
    from .subjects import chart as get_chart

    await require(s, "read")
    return {"success": True, "data": await get_chart(s, book_id)}


@router.post("/books/{book_id}/chart-accounts")
async def add_account(book_id: int, p: AccountIn, s=Depends(db)):
    return {
        "success": True,
        "data": await svc.create_account(s, book_id, p.model_dump()),
    }


@router.get("/books/{book_id}/vouchers")
async def vouchers(
    book_id: int,
    s=Depends(db),
    start: date | None = None,
    end: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    from .queries import list_vouchers

    await require(s, "read")
    result = await list_vouchers(
        s, book_id, start=start, end=end, offset=offset, limit=limit
    )
    return {"success": True, "data": result.pop("rows"), **result}


@router.post("/books/{book_id}/vouchers")
async def new_voucher(
    book_id: int,
    p: VoucherIn,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    await require(s, "create")
    data = p.model_dump(mode="json")
    return {
        "success": True,
        "data": await svc.idempotent(
            s,
            key,
            {"action": "create", "book": book_id, **data},
            lambda: svc.create_voucher(
                s, book_id, data, kind="opening" if p.opening else "ordinary"
            ),
        ),
    }


@router.get("/books/{book_id}/vouchers/{voucher_id}")
async def voucher_detail(book_id: int, voucher_id: int, s=Depends(db)):
    await require(s, "read")
    result = await svc.detail(s, voucher_id, book_id)
    svc.event(s, "read_voucher", book_id=book_id, voucher_id=voucher_id)
    return {"success": True, "data": result}


@router.patch("/books/{book_id}/vouchers/{voucher_id}")
async def patch_voucher(book_id: int, voucher_id: int, p: VoucherPatch, s=Depends(db)):
    return {
        "success": True,
        "data": await svc.update_voucher(
            s, book_id, voucher_id, p.model_dump(mode="json")
        ),
    }


@router.post("/books/{book_id}/vouchers/{voucher_id}/reversal-drafts")
async def reverse(
    book_id: int,
    voucher_id: int,
    p: ReverseIn,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    await require(s, "delete")
    return {
        "success": True,
        "data": await svc.idempotent(
            s,
            key,
            {
                "action": "reverse",
                "book": book_id,
                "voucher": voucher_id,
                **p.model_dump(mode="json"),
            },
            lambda: svc.reversal(s, book_id, voucher_id, p.accounting_date, p.reason),
        ),
    }


@router.post("/books/{book_id}/vouchers/{voucher_id}/{action}")
async def voucher_action(
    book_id: int,
    voucher_id: int,
    action: Literal[
        "submit", "withdraw", "approve", "reject", "revoke-approval", "post", "void"
    ],
    p: ActionIn,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    # Check live authorization even when replaying a receipt after revocation.
    await require(
        s,
        "admin"
        if action in {"approve", "reject", "revoke-approval", "post"}
        else "delete"
        if action == "void"
        else "update",
    )
    return {
        "success": True,
        "data": await svc.idempotent(
            s,
            key,
            {
                "action": action,
                "book": book_id,
                "voucher": voucher_id,
                **p.model_dump(),
            },
            lambda: svc.action(
                s, book_id, voucher_id, action, p.expected_version, p.reason
            ),
        ),
    }


@router.get("/books/{book_id}/trial-balance")
async def trial(book_id: int, as_of: date | None = None, s=Depends(db)):
    await require(s, "read")
    result = await svc.trial_balance(s, book_id, as_of)
    svc.event(s, "read_trial_balance", book_id=book_id)
    return {"success": True, "data": result}


@router.get("/books/{book_id}/reports")
async def report(book_id: int, start: date, end: date, s=Depends(db)):
    await require(s, "read")
    return {"success": True, "data": await svc.reports(s, book_id, start, end)}


@router.get("/books/{book_id}/events")
async def events(book_id: int, s=Depends(db), limit: int = Query(100, ge=1, le=500)):
    await require(s, "read")
    await svc.get_book(s, book_id)
    rows = (
        await s.exec(
            select(AccountingEvent)
            .where(AccountingEvent.book_id == book_id)
            .order_by(AccountingEvent.id.desc())
            .limit(limit)
        )
    ).all()
    from .presentation import event_view

    return {"success": True, "data": [event_view(e) for e in rows]}


@router.get("/permission-events")
async def permission_events(s=Depends(db)):
    await require(s, "admin")
    rows = (
        await s.exec(
            select(AccountingEvent)
            .where(AccountingEvent.book_id == None)
            .order_by(AccountingEvent.id.desc())
            .limit(200)
        )
    ).all()
    from .presentation import event_view

    return {"success": True, "data": [event_view(e) for e in rows]}


@router.get("/books/{book_id}/business-drafts")
async def business_drafts(book_id: int, s=Depends(db)):
    await require(s, "read")
    await svc.get_book(s, book_id)
    rows = (
        await s.exec(
            select(BusinessDraft)
            .where(BusinessDraft.book_id == book_id)
            .order_by(BusinessDraft.id.desc())
            .limit(500)
        )
    ).all()
    svc.event(s, "read_business_drafts", book_id=book_id)
    return {"success": True, "data": [x.model_dump(mode="json") for x in rows]}


class DraftLink(Strict):
    voucher_id: int | None = None
    void: bool = False


@router.post("/books/{book_id}/business-drafts/{draft_id}/resolve")
async def resolve_draft(book_id: int, draft_id: int, p: DraftLink, s=Depends(db)):
    await require(s, "delete" if p.void else "update")
    d = await s.get(BusinessDraft, draft_id)
    if not d or d.book_id != book_id:
        raise AccountingError("DRAFT_NOT_FOUND", "业务草稿不存在", 404)
    if d.state != "pending":
        raise AccountingError("DRAFT_RESOLVED", "业务草稿已处理", 409)
    if p.void:
        d.state = "void"
    else:
        if p.voucher_id is None:
            raise AccountingError("VOUCHER_REQUIRED", "请选择凭证")
        v = await svc.get_voucher(s, book_id, p.voucher_id)
        d.voucher_id = v.id
        d.state = "linked"
    s.add(d)
    svc.event(
        s,
        "resolve_business_draft",
        book_id=book_id,
        draft_id=d.id,
        state=d.state,
        voucher_id=d.voucher_id,
    )
    return {"success": True, "data": d.model_dump(mode="json")}


@router.post("/books/{book_id}/periods/{month}/pre-close")
async def preclose(book_id: int, month: str, s=Depends(db)):
    await require(s, "admin")
    from .closing import close_checks

    return {"success": True, "data": await close_checks(s, book_id, month)}


class BusinessIn(Strict):
    source_key: str = Field(min_length=1, max_length=200)
    facts: dict


@router.post("/books/{book_id}/business-drafts")
async def new_business(
    book_id: int,
    p: BusinessIn,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    await require(s, "create")

    async def create():
        item = await svc.business_draft(s, book_id, p.source_key, p.facts)
        return item.model_dump(mode="json")

    return {
        "success": True,
        "data": await svc.idempotent(
            s, key, {"action": "business", "book": book_id, **p.model_dump()}, create
        ),
    }


@router.post("/books/{book_id}/imports")
async def import_business(book_id: int, file: UploadFile = File(...), s=Depends(db)):
    from .ingestion import import_file

    content = await file.read(8 * 1024 * 1024 + 1)
    return {
        "success": True,
        "data": await import_file(s, book_id, file.filename or "upload", content),
    }


class ZeroOpening(Strict):
    confirm: str


@router.post("/books/{book_id}/opening/zero")
async def zero_opening(book_id: int, p: ZeroOpening, s=Depends(db)):
    await require(s, "admin")
    book = await svc.writable_book(s, book_id)
    if p.confirm != "CONFIRM_ALL_ZERO":
        raise AccountingError("CONFIRM_REQUIRED", "须明确确认所有科目期初为零")
    if book.opening_finalized:
        raise AccountingError("OPENING_LOCKED", "期初已核定", 409)
    existing = (
        await s.exec(
            select(Voucher).where(Voucher.book_id == book_id, Voucher.state != "void")
        )
    ).first()
    if existing:
        raise AccountingError("OPENING_EXISTS", "已有凭证，不能直接核定零期初", 409)
    book.opening_finalized = True
    s.add(book)
    svc.event(s, "confirm_zero_opening", book_id=book_id)
    return {"success": True, "data": book.model_dump(mode="json")}


class OutstandingIn(Strict):
    side: Literal["bank", "book"]
    amount: str
    reason: str = Field(min_length=1, max_length=512)


class ReconcileIn(Strict):
    account_id: int
    as_of: date
    statement_balance: str
    outstanding: list[OutstandingIn] = Field(default_factory=list, max_length=500)
    reason: str = Field(min_length=1, max_length=1024)


@router.post("/books/{book_id}/reconciliations")
async def reconcile(book_id: int, p: ReconcileIn, s=Depends(db)):
    from .closing import reconciliation

    return {
        "success": True,
        "data": await reconciliation(
            s,
            book_id,
            p.account_id,
            p.as_of,
            p.statement_balance,
            [item.model_dump() for item in p.outstanding],
            p.reason,
        ),
    }


@router.post("/books/{book_id}/periods/{month}/closing-drafts")
async def closing_draft(
    book_id: int,
    month: str,
    annual: bool = False,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    from .closing import closing_draft as generate

    await require(s, "admin")
    return {
        "success": True,
        "data": await svc.idempotent(
            s,
            key,
            {"action": "closing", "book": book_id, "month": month, "annual": annual},
            lambda: generate(s, book_id, month, annual),
        ),
    }


@router.post("/books/{book_id}/periods/{month}/close")
async def close_period(
    book_id: int,
    month: str,
    s=Depends(db),
    key: str = Header(default="", alias="Idempotency-Key"),
):
    from .closing import close

    await require(s, "admin")
    return {
        "success": True,
        "data": await svc.idempotent(
            s,
            key,
            {"action": "close", "book": book_id, "month": month},
            lambda: close(s, book_id, month),
        ),
    }


@router.get("/backup")
async def export_accounting_backup(s=Depends(db)):
    from ..services.backup import build_backup

    await require(s, "admin")
    svc.event(s, "export_full_backup")
    return {"success": True, "data": await build_backup(s, note="会计与权限全量备份")}


class AccountPatch(Strict):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool
    quantity_enabled: bool
    unit: str = Field(min_length=1, max_length=16)
    expected_version: int


@router.patch("/books/{book_id}/chart-accounts/{account_id}")
async def update_chart_account(
    book_id: int, account_id: int, p: AccountPatch, s=Depends(db)
):
    from .subjects import update_account

    return {
        "success": True,
        "data": await update_account(s, book_id, account_id, p.model_dump()),
    }


class ExpenseCategoryIn(Strict):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    expected_version: int | None = None


class ExpenseBudgetIn(Strict):
    category_id: int
    month: str
    amount: str


@router.get("/books/{book_id}/expense-categories")
async def expense_categories(book_id: int, s=Depends(db)):
    from .budgets import categories

    await require(s, "read")
    return {
        "success": True,
        "data": [c.model_dump() for c in await categories(s, book_id)],
    }


@router.post("/books/{book_id}/expense-categories")
async def create_expense_category(book_id: int, p: ExpenseCategoryIn, s=Depends(db)):
    from .budgets import save_category

    return {
        "success": True,
        "data": await save_category(s, book_id, p.name, enabled=p.enabled),
    }


@router.patch("/books/{book_id}/expense-categories/{category_id}")
async def patch_expense_category(
    book_id: int, category_id: int, p: ExpenseCategoryIn, s=Depends(db)
):
    from .budgets import save_category

    return {
        "success": True,
        "data": await save_category(
            s, book_id, p.name, category_id, p.enabled, p.expected_version
        ),
    }


@router.get("/books/{book_id}/expense-budgets")
async def expense_budgets(book_id: int, month: str, s=Depends(db)):
    from .budgets import overview

    await require(s, "read")
    return {"success": True, "data": await overview(s, book_id, month)}


@router.put("/books/{book_id}/expense-budgets")
async def put_expense_budget(book_id: int, p: ExpenseBudgetIn, s=Depends(db)):
    from .budgets import set_budget

    return {
        "success": True,
        "data": await set_budget(s, book_id, p.category_id, p.month, p.amount),
    }


class BookPatch(Strict):
    name: str = Field(min_length=1, max_length=100)
    starts_on: date
    expected_version: int
    archived: bool | None = None


@router.get("/books/{book_id}")
async def book_detail(book_id: int, s=Depends(db)):
    from .books import detail

    await require(s, "read")
    result = await detail(s, book_id)
    svc.event(s, "read_book", book_id=book_id)
    return {"success": True, "data": result}


@router.patch("/books/{book_id}")
async def patch_book(book_id: int, p: BookPatch, s=Depends(db)):
    from .books import update

    return {
        "success": True,
        "data": await update(
            s, book_id, p.name, p.starts_on, p.expected_version, p.archived
        ),
    }


@router.delete("/books/{book_id}")
async def delete_book(book_id: int, s=Depends(db)):
    from .books import archive

    return {"success": True, "data": await archive(s, book_id)}


@router.post("/books/{book_id}/activate")
async def activate_book(book_id: int, s=Depends(db)):
    from .books import activate

    return {"success": True, "data": await activate(s, book_id)}


@router.get("/operation-events")
async def operation_events(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=200),
    start: date | None = None,
    end: date | None = None,
    s=Depends(db),
):
    from .operations import feed

    await require(s, "admin")
    if start and end and end < start:
        raise AccountingError("DATE_RANGE", "结束日期不能早于开始日期")
    return {"success": True, "data": await feed(s, offset, limit, start, end)}
