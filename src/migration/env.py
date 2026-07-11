from contextlib import suppress
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from db.base import Base
from db.settings import DatabaseSettings

# Registers every table on `Base.metadata` — required before `target_metadata`
# is read below, and before `--autogenerate` can see any of these tables.
# `accounting` and `trades` are independent packages (see docs/architecture.md:
# deleting either one should never break the other), so each import is guarded —
# a tree with only one of them still generates/runs migrations for that one.
import db.models  # noqa: F401
with suppress(ModuleNotFoundError):
    import accounting.db  # noqa: F401
with suppress(ModuleNotFoundError):
    import trades.db  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The connection string always comes from `.env` (via `DatabaseSettings`), never
# from `alembic.ini` — one source of truth for where Postgres lives, shared with
# the app itself (`db.session.get_engine`).
config.set_main_option("sqlalchemy.url", DatabaseSettings().database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
