"""Add accounting tables without modifying historical financial records."""

from alembic import op
from firefly.accounting import models

revision = "0001_accounting"
down_revision = None
branch_labels = None
depends_on = None
MODELS = [
    models.AccessControl,
    models.QQGrant,
    models.Book,
    models.Period,
    models.ChartAccount,
    models.Voucher,
    models.VoucherLine,
    models.AccountingEvent,
    models.CommandReceipt,
    models.BusinessDraft,
    models.Conversation,
    models.Reconciliation,
]


def upgrade():
    bind = op.get_bind()
    for model in MODELS:
        model.__table__.create(bind, checkfirst=True)
    bind.exec_driver_sql(
        "INSERT OR IGNORE INTO ff_access_control (id,admin_qq,version) VALUES (1,'',1)"
    )
    for operation in ("UPDATE", "DELETE"):
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_voucher_posted_{operation.lower()}
            BEFORE {operation} ON ff_voucher WHEN OLD.state='posted'
            BEGIN SELECT RAISE(ABORT,'Posted voucher is immutable'); END""")
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_line_posted_{operation.lower()}
            BEFORE {operation} ON ff_voucher_line
            WHEN EXISTS(SELECT 1 FROM ff_voucher WHERE id=OLD.voucher_id AND state='posted')
            BEGIN SELECT RAISE(ABORT,'Posted lines are immutable'); END""")
        bind.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS ff_event_{operation.lower()}
            BEFORE {operation} ON ff_accounting_event
            BEGIN SELECT RAISE(ABORT,'Accounting audit is append only'); END""")
    bind.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS ff_line_posted_insert
        BEFORE INSERT ON ff_voucher_line
        WHEN EXISTS(SELECT 1 FROM ff_voucher WHERE id=NEW.voucher_id AND state='posted')
        BEGIN SELECT RAISE(ABORT,'Cannot append posted lines'); END""")
    bind.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS ff_line_move_posted
        BEFORE UPDATE ON ff_voucher_line
        WHEN EXISTS(SELECT 1 FROM ff_voucher WHERE id=NEW.voucher_id AND state='posted')
        BEGIN SELECT RAISE(ABORT,'Cannot move lines into posted voucher'); END""")


def downgrade():
    raise RuntimeError(
        "Accounting downgrade is prohibited: restore a verified backup to an isolated database"
    )
