"""Alembic reads DATABASE_URL only when migrations are explicitly invoked."""

import os

from alembic import context
from durable_agent.db.models import Base
from durable_agent.db.session import postgres_url
from sqlalchemy import Connection, create_engine
from sqlalchemy.pool import NullPool

config = context.config
target_metadata = Base.metadata


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=postgres_url(os.environ["DATABASE_URL"]),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    migrate(connection)
else:
    engine = create_engine(postgres_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
