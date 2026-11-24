import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema

from app.models import SCHEMA, Base
from app.settings import Settings

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url.get_secret_value()


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    return type_ != "schema" or name == SCHEMA


def configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=Base.metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
        **kwargs,
    )


def run_migrations(connection: Connection) -> None:
    connection.execute(CreateSchema(SCHEMA, if_not_exists=True))
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    configure(url=database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
