"""Alembic environment. Migrations run as the schema owner role (DATABASE_URL_MIGRATIONS)."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _migration_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL_MIGRATIONS")
    if not url:
        raise RuntimeError("DATABASE_URL_MIGRATIONS is required to run migrations")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL_MIGRATIONS must use the postgresql+psycopg:// scheme")
    return str(url)


def run_migrations_online() -> None:
    engine = create_engine(_migration_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported")
run_migrations_online()
