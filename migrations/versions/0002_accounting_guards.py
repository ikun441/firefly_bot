"""Reconciliation fingerprints and cross-book line integrity."""

from alembic import op
from sqlalchemy import Column, String, inspect

revision = "0002_accounting_guards"
down_revision = "0001_accounting"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "ledger_fingerprint" not in {
        c["name"] for c in inspect(bind).get_columns("ff_reconciliation")
    }:
        op.add_column(
            "ff_reconciliation",
            Column("ledger_fingerprint", String(), nullable=False, server_default=""),
        )
    for operation in ("INSERT", "UPDATE"):
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_line_book_{operation.lower()}
            BEFORE {operation} ON ff_voucher_line
            WHEN NOT EXISTS(
                SELECT 1 FROM ff_voucher v JOIN ff_chart_account a ON a.book_id=v.book_id
                WHERE v.id=NEW.voucher_id AND a.id=NEW.account_id)
            BEGIN SELECT RAISE(ABORT,'Voucher line account belongs to another book'); END""")
    bind.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS ff_voucher_book_update
        BEFORE UPDATE OF book_id ON ff_voucher
        WHEN EXISTS(SELECT 1 FROM ff_voucher_line l JOIN ff_chart_account a ON a.id=l.account_id
                    WHERE l.voucher_id=OLD.id AND a.book_id!=NEW.book_id)
        BEGIN SELECT RAISE(ABORT,'Voucher cannot change book'); END""")
    for operation in ("UPDATE", "DELETE"):
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_reconciliation_{operation.lower()}
            BEFORE {operation} ON ff_reconciliation
            BEGIN SELECT RAISE(ABORT,'Reconciliation is append only'); END""")


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to an isolated database instead of downgrading"
    )
