"""Account hierarchy, quantity accounting and expense budgets."""

from alembic import op
from sqlalchemy import inspect
from firefly.accounting.models import ExpenseCategory, ExpenseBudget

revision = "0003_dimensions_budgets"
down_revision = "0002_accounting_guards"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    ExpenseCategory.__table__.create(bind, checkfirst=True)
    ExpenseBudget.__table__.create(bind, checkfirst=True)
    additions = {
        "ff_chart_account": {
            "parent_id": "INTEGER REFERENCES ff_chart_account(id)",
            "quantity_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "unit": "VARCHAR NOT NULL DEFAULT '件'",
            "version": "INTEGER NOT NULL DEFAULT 1",
        },
        "ff_voucher_line": {
            "budget_category_id": "INTEGER REFERENCES ff_expense_category(id)",
            "summary": "VARCHAR NOT NULL DEFAULT ''",
            "quantity": "VARCHAR",
            "unit_price": "VARCHAR",
            "unit": "VARCHAR NOT NULL DEFAULT ''",
        },
    }
    for table, columns in additions.items():
        existing = {c["name"] for c in inspect(bind).get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                bind.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )
    for action in ("INSERT", "UPDATE"):
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_budget_line_{action.lower()}
            BEFORE {action} ON ff_voucher_line WHEN NEW.budget_category_id IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM ff_expense_category c JOIN ff_chart_account a ON a.id=NEW.account_id
                WHERE c.id=NEW.budget_category_id AND c.book_id=a.book_id AND a.kind='expense')
            BEGIN SELECT RAISE(ABORT,'Expense category belongs to another book'); END""")
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_leaf_line_{action.lower()}
            BEFORE {action} ON ff_voucher_line WHEN EXISTS(
                SELECT 1 FROM ff_chart_account WHERE parent_id=NEW.account_id)
                AND EXISTS(SELECT 1 FROM ff_voucher WHERE id=NEW.voucher_id
                    AND kind NOT IN ('reversal','closing','annual_closing'))
            BEGIN SELECT RAISE(ABORT,'Only leaf accounts accept new entries'); END""")


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to an isolated database instead of downgrading"
    )
