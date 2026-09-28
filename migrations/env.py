# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Alembic migration environment configuration."""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models to ensure they are registered with Base.metadata
from app.atlasclaw.db.models import Base
from app.atlasclaw.core.config import get_config
from app.atlasclaw.db.database import (
    build_mysql_connect_args,
    resolve_configured_database_url,
    _resolve_mysql_tls,
)

logger = logging.getLogger("alembic.env")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Alembic logging without disabling the
# application loggers that were configured by uvicorn before startup migrations.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def _resolve_configured_url(db_config: object) -> str:
    """Resolve the database URL from a configured database section (raises on failure)."""
    return resolve_configured_database_url(db_config)


def get_url() -> str:
    """Get database URL from configuration.

    A configured database section must resolve: silently migrating a stray
    SQLite file while the application runs on MySQL leaves the real schema
    unupgraded and still reports success. Only a deployment without any
    database section (development/test) falls back to the alembic.ini URL.
    """
    try:
        atlasclaw_config = get_config()
    except Exception as exc:
        atlasclaw_config = None
        logger.warning(
            "Could not load atlasclaw.json for migration URL resolution (%s); "
            "falling back to alembic.ini sqlalchemy.url",
            exc,
        )

    if atlasclaw_config is not None:
        db_config = getattr(atlasclaw_config, "database", None)
        if db_config is not None:
            return _resolve_configured_url(db_config)

    logger.warning(
        "No database section in atlasclaw.json; falling back to alembic.ini "
        "sqlalchemy.url"
    )
    return config.get_main_option("sqlalchemy.url", "sqlite+aiosqlite:///./data/atlasclaw.db")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    url = get_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    # Apply TLS connect_args for MySQL connections
    connect_args: dict = {}
    if url.startswith("mysql"):
        connect_args = build_mysql_connect_args(_resolve_mysql_tls())

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
