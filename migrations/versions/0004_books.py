"""Multiple book selection and recoverable archival."""

from alembic import op
from sqlalchemy import inspect

revision = "0004_books"
down_revision = "0003_dimensions_budgets"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    additions = {
        "ff_book": {
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "version": "INTEGER NOT NULL DEFAULT 1",
        },
        "ff_access_control": {"active_book_id": "INTEGER REFERENCES ff_book(id)"},
    }
    for table, columns in additions.items():
        existing = {c["name"] for c in inspect(bind).get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                bind.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )
    bind.exec_driver_sql(
        "UPDATE ff_access_control SET active_book_id=(SELECT id FROM ff_book WHERE archived=0 ORDER BY id LIMIT 1) WHERE active_book_id IS NULL"
    )


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to an isolated database instead of downgrading"
    )
