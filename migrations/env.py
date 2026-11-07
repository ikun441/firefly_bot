from alembic import context
from firefly.accounting import models as models
from sqlmodel import SQLModel

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "Use the application migration runner with an explicit connection"
    )
context.configure(connection=connection, target_metadata=SQLModel.metadata)
with context.begin_transaction():
    context.run_migrations()
