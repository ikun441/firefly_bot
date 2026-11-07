"""Additive v2 tables; legacy ledger remains separate."""

from datetime import date, datetime, timezone
from typing import Optional
from sqlalchemy import Column, UniqueConstraint, CheckConstraint, BigInteger
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


def now():
    return datetime.now(timezone.utc)


class AccessControl(SQLModel, table=True):
    __tablename__ = "ff_access_control"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: int = Field(default=1, primary_key=True)
    admin_qq: str = Field(default="", max_length=20)
    version: int = 1
    active_book_id: Optional[int] = Field(default=None, foreign_key="ff_book.id")


class QQGrant(SQLModel, table=True):
    __tablename__ = "ff_qq_grant"
    id: Optional[int] = Field(default=None, primary_key=True)
    qq: str = Field(unique=True, index=True, max_length=20)
    nickname: str = Field(default="", max_length=64)
    enabled: bool = True
    can_create: bool = False
    can_read: bool = False
    can_update: bool = False
    can_delete: bool = False
    version: int = 1


class Book(SQLModel, table=True):
    __tablename__ = "ff_book"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    standard: str = "small_enterprise"
    currency: str = "CNY"
    starts_on: date
    opening_finalized: bool = False
    legacy_frozen: bool = True
    archived: bool = False
    version: int = 1


class Period(SQLModel, table=True):
    __tablename__ = "ff_period"
    __table_args__ = (UniqueConstraint("book_id", "month"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    month: str = Field(max_length=7)
    closed: bool = False
    next_number: int = 1


class ChartAccount(SQLModel, table=True):
    __tablename__ = "ff_chart_account"
    __table_args__ = (UniqueConstraint("book_id", "code"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    code: str
    name: str
    kind: str
    normal_side: str
    cash: bool = False
    enabled: bool = True
    parent_id: Optional[int] = Field(default=None, foreign_key="ff_chart_account.id")
    quantity_enabled: bool = False
    unit: str = "件"
    version: int = 1


class Voucher(SQLModel, table=True):
    __tablename__ = "ff_voucher"
    __table_args__ = (
        UniqueConstraint("book_id", "number"),
        UniqueConstraint("reverses_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    accounting_date: date
    summary: str = Field(max_length=512)
    kind: str = "ordinary"
    state: str = "draft"
    number: Optional[str] = None
    version: int = 1
    created_by: dict = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    approved_by: dict = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    posted_by: dict = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    reverses_id: Optional[int] = Field(default=None, foreign_key="ff_voucher.id")
    created_at: datetime = Field(default_factory=now)
    reason: str = ""


class VoucherLine(SQLModel, table=True):
    __tablename__ = "ff_voucher_line"
    __table_args__ = (
        CheckConstraint("amount_minor > 0 AND amount_minor <= 10000000000"),
        CheckConstraint("side IN ('debit','credit')"),
        CheckConstraint("effect_sign IN (-1,1)"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    voucher_id: int = Field(foreign_key="ff_voucher.id", index=True)
    account_id: int = Field(foreign_key="ff_chart_account.id", index=True)
    side: str
    amount_minor: int = Field(sa_column=Column(BigInteger, nullable=False))
    effect_sign: int = 1
    auxiliary: str = Field(default="", max_length=128)
    summary: str = Field(default="", max_length=512)
    cashflow: str = Field(default="", max_length=32)
    budget_category_id: Optional[int] = Field(
        default=None, foreign_key="ff_expense_category.id"
    )
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    unit: str = ""


class AccountingEvent(SQLModel, table=True):
    __tablename__ = "ff_accounting_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: Optional[int] = Field(default=None, foreign_key="ff_book.id", index=True)
    voucher_id: Optional[int] = Field(
        default=None, foreign_key="ff_voucher.id", index=True
    )
    action: str
    actor: dict = Field(sa_column=Column(JSON, nullable=False))
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class CommandReceipt(SQLModel, table=True):
    __tablename__ = "ff_command_receipt"
    __table_args__ = (UniqueConstraint("actor_key", "key"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_key: str
    key: str = Field(max_length=200)
    fingerprint: str
    result: dict = Field(sa_column=Column(JSON, nullable=False))


class BusinessDraft(SQLModel, table=True):
    __tablename__ = "ff_business_draft"
    __table_args__ = (UniqueConstraint("book_id", "source_key"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    source_key: str = Field(max_length=200)
    facts: dict = Field(sa_column=Column(JSON, nullable=False))
    actor: dict = Field(sa_column=Column(JSON, nullable=False))
    state: str = "pending"
    voucher_id: Optional[int] = Field(default=None, foreign_key="ff_voucher.id")
    created_at: datetime = Field(default_factory=now)


class Conversation(SQLModel, table=True):
    __tablename__ = "ff_accounting_conversation"
    session_key: str = Field(primary_key=True, max_length=64)
    owner: str
    facts: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    state: str = "pending"
    revision: int = 1
    expires_at: datetime = Field(default_factory=now)


class Reconciliation(SQLModel, table=True):
    __tablename__ = "ff_reconciliation"
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    account_id: int = Field(foreign_key="ff_chart_account.id")
    as_of: date
    statement_minor: int
    ledger_minor: int
    ledger_fingerprint: str = ""
    items: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    reason: str


class ExpenseCategory(SQLModel, table=True):
    __tablename__ = "ff_expense_category"
    __table_args__ = (UniqueConstraint("book_id", "name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    name: str = Field(max_length=64)
    enabled: bool = True
    version: int = 1


class ExpenseBudget(SQLModel, table=True):
    __tablename__ = "ff_expense_budget"
    __table_args__ = (UniqueConstraint("book_id", "category_id", "month"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="ff_book.id", index=True)
    category_id: int = Field(foreign_key="ff_expense_category.id")
    month: str = Field(max_length=7)
    amount_minor: int = Field(sa_column=Column(BigInteger, nullable=False))
