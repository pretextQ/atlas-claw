# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Shared fixtures for the AtlasClaw test suite."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def restore_api_context():
    """Restore the process-wide API context after every test.

    ``api.deps_context.set_api_context`` stores a module-level global, and
    many test modules point it at their own temporary workspace. Without a
    restore, the last test to run decides what every later module sees, which
    shows up as order-dependent failures (wrong permissions, wrong workspace).
    """
    from app.atlasclaw.api import deps_context

    previous = deps_context._api_context
    yield
    deps_context._api_context = previous


@pytest.fixture(autouse=True)
def restore_channel_registry():
    """Restore channel registry state after every test.

    Registry handlers, instances, and connections are class-level state; a
    test that registers a stub handler must not leak it into later modules.
    """
    from app.atlasclaw.channels.registry import ChannelRegistry

    handlers = dict(ChannelRegistry._handlers)
    instances = dict(ChannelRegistry._instances)
    connections = dict(ChannelRegistry._connections)
    yield
    ChannelRegistry._handlers.clear()
    ChannelRegistry._handlers.update(handlers)
    ChannelRegistry._instances.clear()
    ChannelRegistry._instances.update(instances)
    ChannelRegistry._connections.clear()
    ChannelRegistry._connections.update(connections)


@pytest.fixture(autouse=True)
def restore_database_manager():
    """Restore the process-wide database manager after every test.

    ``DatabaseManager`` is a singleton whose engine, session factory, and
    config live on the class, and tests point it at their own temporary SQLite
    file. Without a restore, the next module talks to the previous test's
    database (or to a disposed engine), which shows up as order-dependent 403s
    and "database is locked" failures.
    """
    from app.atlasclaw.db import database as db_module

    manager_cls = db_module.DatabaseManager
    saved_global = db_module._db_manager
    saved_instance = manager_cls._instance
    saved_engine = manager_cls._engine
    saved_factory = manager_cls._session_factory
    saved_config = manager_cls._config

    yield

    created_engine = manager_cls._engine
    db_module._db_manager = saved_global
    manager_cls._instance = saved_instance
    manager_cls._engine = saved_engine
    manager_cls._session_factory = saved_factory
    manager_cls._config = saved_config

    if created_engine is not None and created_engine is not saved_engine:
        # Release the engine the test created so it stops holding its SQLite
        # file open for later modules.
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(created_engine.dispose())
            finally:
                loop.close()
        except Exception:
            pass
