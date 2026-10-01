"""PostgreSQL tests use a fresh schema and apply real migrations."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from durable_agent.db.session import postgres_url
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture
def migration_config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


@pytest.fixture
def postgres_engine(migration_config):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")
    url = postgres_url(database_url)
    schema = f"test_{uuid4().hex}"
    admin = create_engine(url)
    engine = create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with engine.begin() as connection:
            migration_config.attributes["connection"] = connection
            command.upgrade(migration_config, "head")
        migration_config.attributes.pop("connection")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
