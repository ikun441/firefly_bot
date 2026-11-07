"""Versioned additive migrations on the existing transaction connection."""

from pathlib import Path
from alembic import command
from alembic.config import Config


def upgrade(connection):
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Accounting v2 currently requires SQLite")
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")
