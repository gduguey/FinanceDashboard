from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Registers every table on `Base.metadata` — required before `target_metadata`
# is read below, and before `--autogenerate` can see any of these tables.
import db.models  # noqa: F401
from db.base import Base
from db.settings import DatabaseSettings


def _import_optional_package(name: str) -> None:
    """Import a table-registering package, tolerating only its own absence.

    `accounting` and `trades` are independent packages (see
    docs/architecture.md: deleting either one should never break the other),
    so a tree with only one of them still generates/runs migrations for that
    one. Only the top-level package being absent is swallowed — a
    `ModuleNotFoundError` for a *different* module (a real broken import
    inside the package) is re-raised, so it can't silently register zero
    tables and make `--autogenerate` emit destructive DROPs.

    Raises
    ------
    ModuleNotFoundError
        If a module *inside* `name` is missing, rather than `name` itself.
    """
    try:
        __import__(name)
    except ModuleNotFoundError as error:
        if error.name != name and error.name != name.split(".", 1)[0]:
            raise


_import_optional_package("accounting.db")
_import_optional_package("trades.db")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The connection string always comes from `.env` (via `DatabaseSettings`), never
# from `alembic.ini` — one source of truth for where Postgres lives, shared with
# the app itself (`db.session.get_engine`). `%` is doubled because
# `set_main_option` stores into a ConfigParser, which treats a bare `%` as
# interpolation syntax — a password containing one would otherwise crash here.
config.set_main_option("sqlalchemy.url", DatabaseSettings().database_url.replace("%", "%%"))

# Interpret the config file for Python logging.
#
# `disable_existing_loggers=False` is not the default and is not cosmetic.
# `fileConfig` otherwise sets `disabled = True` on every logger that already
# exists and is not named in `alembic.ini` — which, whenever migrations run
# **in-process**, means every application logger imported before this line
# goes silent for the rest of that process. Production never notices
# (`deploy/Dockerfile` runs `alembic upgrade head` as its own process before
# uvicorn starts), but the test suite runs migrations in-process for every
# scratch database, so any suite ordered after one of those was asserting on
# logs that could no longer be emitted — silently, since a disabled logger
# raises nothing. Found by exactly that: a fail-closed test in
# `tests/trades/api/test_auth.py` passed alone and saw zero records in a full
# run.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

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
