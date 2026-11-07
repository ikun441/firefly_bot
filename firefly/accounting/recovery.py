"""Restore a complete backup into a new database, never over a running ledger."""

import os
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from firefly.services.backup import BACKUP_TABLES, validate_backup
from .domain import balanced
from .migrations import upgrade
from .models import Book, ChartAccount, Period, Voucher, VoucherLine
from .service import trial_balance


def validate_rows(data):
    valid, message = validate_backup(data)
    if not valid or data.get("version") not in {2, 3}:
        raise ValueError(message if not valid else "离线恢复工具仅支持完整 v2 备份")
    parsed = {}
    for name, model in BACKUP_TABLES:
        parsed[name] = []
        for row in data["tables"].get(name, []):
            added = {
                "chart_accounts": {"parent_id", "quantity_enabled", "unit", "version"},
                "books": {"archived", "version"},
                "access_control": {"active_book_id"},
                "voucher_lines": {
                    "budget_category_id",
                    "quantity",
                    "unit_price",
                    "unit",
                    "summary",
                },
            }
            expected = set(model.model_fields) - (
                added.get(name, set()) if data["version"] == 2 else set()
            )
            if set(row) != expected:
                raise ValueError(f"{name} 字段缺失或包含未知字段")
            instance = model.model_validate(row)
            values = instance.model_dump()
            # Identity columns must be restored exactly, never generated silently.
            for column in model.__table__.primary_key:
                if values[column.name] is None:
                    raise ValueError(f"{name} 缺少主键")
            parsed[name].append(values)
    control = parsed["access_control"]
    if len(control) != 1 or control[0]["id"] != 1:
        raise ValueError("唯一管理员控制记录无效")
    if control[0].get("active_book_id") is not None and control[0][
        "active_book_id"
    ] not in {b["id"] for b in parsed["books"]}:
        raise ValueError("当前账套引用不存在")
    return parsed


async def verify_ledger(session):
    books = (await session.exec(select(Book))).all()
    accounts = {a.id: a for a in (await session.exec(select(ChartAccount))).all()}
    vouchers = {v.id: v for v in (await session.exec(select(Voucher))).all()}
    lines = (await session.exec(select(VoucherLine))).all()
    groups = {vid: [] for vid in vouchers}
    allowed_states = {"draft", "submitted", "approved", "posted", "void"}
    allowed_kinds = {"ordinary", "opening", "reversal", "closing", "annual_closing"}
    for line in lines:
        voucher, account = vouchers[line.voucher_id], accounts[line.account_id]
        if voucher.book_id != account.book_id:
            raise ValueError("分录存在跨账套科目引用")
        if voucher.kind != "reversal" and line.effect_sign != 1:
            raise ValueError("普通凭证存在非法红字")
        groups[voucher.id].append(line.model_dump())
    periods = {
        (p.book_id, p.month): p for p in (await session.exec(select(Period))).all()
    }
    for voucher in vouchers.values():
        if voucher.state not in allowed_states or voucher.kind not in allowed_kinds:
            raise ValueError("凭证状态或种类无效")
        balanced(groups[voucher.id])
        if voucher.state == "posted":
            period = periods.get(
                (voucher.book_id, voucher.accounting_date.strftime("%Y-%m"))
            )
            prefix = voucher.accounting_date.strftime("%Y-%m") + "-记-"
            if (
                not period
                or not voucher.number
                or not voucher.number.startswith(prefix)
            ):
                raise ValueError("已过账凭证编号或期间无效")
            suffix = voucher.number[len(prefix) :]
            if not suffix.isdigit() or not 0 < int(suffix) < period.next_number:
                raise ValueError("凭证序列值无效")
            if not voucher.posted_by.get("key") or not voucher.approved_by.get("key"):
                raise ValueError("已过账凭证缺少审核或过账身份")
        if voucher.reverses_id:
            original = vouchers[voucher.reverses_id]
            if (
                original.book_id != voucher.book_id
                or original.kind != "ordinary"
                or original.state != "posted"
            ):
                raise ValueError("冲销原凭证无效")
            expected = sorted(
                (r["account_id"], r["side"], r["amount_minor"], -r["effect_sign"])
                for r in groups[original.id]
            )
            actual = sorted(
                (r["account_id"], r["side"], r["amount_minor"], r["effect_sign"])
                for r in groups[voucher.id]
            )
            if voucher.kind != "reversal" or expected != actual:
                raise ValueError("全额冲销分录与原凭证不匹配")
    result = []
    for book in books:
        trial = await trial_balance(session, book.id)
        if not trial["balanced"]:
            raise ValueError("恢复后试算不平衡")
        result.append(
            {
                "book": book.name,
                "debit": trial["debit"],
                "credit": trial["credit"],
                "balanced": True,
            }
        )
    return result


async def restore_to_new_database(data, destination):
    """Publish a verified SQLite file with exclusive creation; existing paths are refused."""
    parsed = validate_rows(data)
    target = Path(destination).expanduser().absolute()
    if target.exists():
        raise ValueError("目标数据库已存在，拒绝覆盖；请指定新的隔离路径")
    if not target.parent.is_dir():
        raise ValueError("目标目录不存在，请先创建隔离目录")
    temporary = target.with_name("." + target.name + ".restore-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    engine = create_async_engine("sqlite+aiosqlite:///" + str(temporary))
    published = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.execute(text("BEGIN IMMEDIATE"))
            await connection.execute(text("PRAGMA defer_foreign_keys=ON"))
            accounting_names = {
                m.__tablename__
                for name, m in BACKUP_TABLES
                if name
                in {
                    "access_control",
                    "qq_grants",
                    "books",
                    "periods",
                    "chart_accounts",
                    "vouchers",
                    "voucher_lines",
                    "accounting_events",
                    "command_receipts",
                    "business_drafts",
                    "accounting_conversations",
                    "reconciliations",
                    "expense_categories",
                    "expense_budgets",
                }
            }
            legacy = [
                table
                for table in SQLModel.metadata.sorted_tables
                if table.name not in accounting_names
            ]
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(sync, tables=legacy)
            )
            await connection.run_sync(upgrade)
            await connection.execute(text("DELETE FROM ff_access_control"))
            final_states = []
            parent_links = []
            for name, model in BACKUP_TABLES:
                for values in parsed[name]:
                    row = dict(values)
                    if model is ChartAccount and row.get("parent_id"):
                        parent_links.append((row["id"], row["parent_id"]))
                        row["parent_id"] = None
                    if model is Voucher:
                        final_states.append((row["id"], row["state"]))
                        # Insert child lines before transitioning to immutable posted state.
                        row["state"] = "draft"
                    await connection.execute(model.__table__.insert().values(**row))
            for account_id, parent_id in parent_links:
                await connection.execute(
                    ChartAccount.__table__.update()
                    .where(ChartAccount.id == account_id)
                    .values(parent_id=parent_id)
                )
            for voucher_id, state in final_states:
                await connection.execute(
                    Voucher.__table__.update()
                    .where(Voucher.id == voucher_id)
                    .values(state=state)
                )
            if (await connection.execute(text("PRAGMA foreign_key_check"))).all():
                raise ValueError("恢复后外键校验失败")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            verification = await verify_ledger(session)
            integrity = (
                await session.execute(text("PRAGMA integrity_check"))
            ).scalar_one()
            if integrity != "ok":
                raise ValueError("SQLite 完整性校验失败")
            counts = {}
            for name, model in BACKUP_TABLES:
                counts[name] = len((await session.exec(select(model))).all())
            expected_counts = {name: len(rows) for name, rows in parsed.items()}
            if counts != expected_counts:
                raise ValueError("恢复后行数与备份不一致")
        await engine.dispose()
        # link fails atomically if another process has created the destination meanwhile.
        os.link(temporary, target)
        published = True
        return {
            "database": str(target),
            "schema_revision": "0004_books",
            "counts": counts,
            "trial_balances": verification,
            "integrity": "ok",
            "notice": "已恢复到隔离数据库；尚未切换服务或覆盖现有数据",
        }
    finally:
        await engine.dispose()
        temporary.unlink(missing_ok=True)
        if not published:
            for suffix in ("-journal", "-wal", "-shm"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
