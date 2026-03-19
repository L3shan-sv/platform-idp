"""
Alembic migration environment.

CRITICAL: Uses DATABASE_URL_MIGRATIONS (direct PostgreSQL port 5432).
NOT the PgBouncer URL (port 6432).
PgBouncer transaction mode is incompatible with DDL transactions.
"""
import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.core.database import Base
target_metadata = Base.metadata

# Always use direct PostgreSQL for migrations — never PgBouncer
database_url = os.environ.get(
    "DATABASE_URL_MIGRATIONS",
    "postgresql+psycopg2://nerve:nerve_dev_secret@localhost:5432/nerve",
)
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
