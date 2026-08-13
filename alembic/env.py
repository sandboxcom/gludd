"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from general_ludd.db.models import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig() otherwise sets
    # .disabled=True on every already-imported general_ludd.* logger.
    # The daemon lifespan runs migrations in-process (stamp_head), so the
    # default would silently kill all application logging — and, in tests,
    # every caplog assertion in the same xdist worker — after first boot.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# D-37: honour DATABASE_URL so `alembic upgrade` on prod does not silently
# migrate the hardcoded sqlite:///./test.db in alembic.ini.
if db_url := os.environ.get("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
