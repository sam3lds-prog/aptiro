"""Alembic environment for Aptiro.

Binds Alembic to the SQLModel metadata defined in app.py and resolves
the database URL from APTIRO_DATABASE_URL (the same variable the app
uses), so migrations target the production Postgres exactly like the
running service. SQLite remains the zero-config dev/test default.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(__file__) + "/..")

# Importing app registers every SQLModel table on SQLModel.metadata.
import app  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

DB_URL = os.getenv(
    "APTIRO_DATABASE_URL",
    "postgresql+psycopg2://aptiro:aptiro@localhost:5432/aptiro",
)
config.set_main_option("sqlalchemy.url", DB_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL, target_metadata=target_metadata,
        literal_binds=True, compare_type=True,
        dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
