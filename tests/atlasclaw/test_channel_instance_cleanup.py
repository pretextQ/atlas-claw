# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-13 regression tests: channel instance cleanup and key uniqueness.

Covers: failed setup/start/connect releases the registry instance
(F-0032/F-0033), the instance key is unambiguous when ids contain colons
(F-0034), and a stop requested while an initialization is in flight tears
the late handler down instead of reporting it stopped.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.channels.registry import ChannelRegistry
from tests.atlasclaw.channel_test_stubs import StubChannelHandler


class _FailingHandler(StubChannelHandler):
    """Handler whose setup/start/connect outcome is configurable."""

    fail_setup = False
    fail_start = False
    fail_connect = False

    async def setup(self, connection_config):
        if self.fail_setup:
            return False
        return await super().setup(connection_config)

    async def start(self, context):
        if self.fail_start:
            return False
        return await super().start(context)

    async def connect(self):
        if self.fail_connect:
            return False
        return await super().connect()


def _patch_channel_db(monkeypatch, *, user_id="user-1", channel_type="websocket",
                      connection_id="conn-1", found=True):
    """Patch the DB lookup used by initialize_connection."""
    mock_channel = MagicMock()
    mock_channel.id = connection_id
    mock_channel.name = "Test Connection"
    mock_channel.type = channel_type
    mock_channel.config = {"token": "x"}
    mock_channel.is_active = True
    mock_channel.is_default = False
    mock_channel.user_id = user_id

    mock_db_manager = MagicMock()
    mock_db_manager.return_value.get_session.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_db_manager.return_value.get_session.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("app.atlasclaw.db.get_db_manager", mock_db_manager)

    mock_service = MagicMock()
    mock_service.get_by_id = AsyncMock(return_value=mock_channel if found else None)
    mock_service.to_channel_config.return_value = {
        "id": connection_id,
        "name": "Test Connection",
        "channel_type": channel_type,
        "config": {"token": "x"},
        "enabled": True,
    }
    monkeypatch.setattr("app.atlasclaw.channels.manager.ChannelConfigService", mock_service)


@pytest.fixture
def manager(monkeypatch):
    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry._connections.clear()
    ChannelRegistry.register("websocket", _FailingHandler)

    instance = ChannelManager("/tmp/atlasclaw-wp13")
    _patch_channel_db(monkeypatch)
    yield instance

    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry._connections.clear()


def _reset_failures():
    _FailingHandler.fail_setup = False
    _FailingHandler.fail_start = False
    _FailingHandler.fail_connect = False


class TestInstanceKeyUniqueness:
    def test_colon_containing_ids_do_not_collide(self):
        """F-0034: ("a:b","c") and ("a","b:c") must not share one key."""
        first = ChannelManager._instance_key("a:b", "c", "d")
        second = ChannelManager._instance_key("a", "b:c", "d")
        assert first != second

    def test_delimiter_ids_round_trip_distinctly(self):
        key = ChannelManager._instance_key("u:1", "web:socket", "conn:1")
        assert key != ChannelManager._instance_key("u", "1:web:socket", "conn:1")


class TestFailurePathReleasesInstance:
    @pytest.mark.asyncio
    async def test_setup_failure_releases_registry_instance(self, manager):
        _reset_failures()
        _FailingHandler.fail_setup = True
        try:
            ok = await manager.initialize_connection("user-1", "websocket", "conn-1")
            assert ok is False
            key = ChannelManager._instance_key("user-1", "websocket", "conn-1")
            assert ChannelRegistry.get_instance(key) is None
            assert key not in manager._active_connections
        finally:
            _reset_failures()

    @pytest.mark.asyncio
    async def test_start_failure_releases_registry_instance(self, manager):
        _reset_failures()
        _FailingHandler.fail_start = True
        try:
            ok = await manager.initialize_connection("user-1", "websocket", "conn-1")
            assert ok is False
            key = ChannelManager._instance_key("user-1", "websocket", "conn-1")
            assert ChannelRegistry.get_instance(key) is None
        finally:
            _reset_failures()

    @pytest.mark.asyncio
    async def test_connect_failure_releases_registry_instance(self, manager):
        _reset_failures()
        _FailingHandler.fail_connect = True
        try:
            ok = await manager.initialize_connection("user-1", "websocket", "conn-1")
            assert ok is False
            key = ChannelManager._instance_key("user-1", "websocket", "conn-1")
            assert ChannelRegistry.get_instance(key) is None
            assert key not in manager._active_connections
        finally:
            _reset_failures()

    @pytest.mark.asyncio
    async def test_repeated_failures_leave_no_registry_entries(self, manager):
        _reset_failures()
        _FailingHandler.fail_setup = True
        try:
            for _ in range(3):
                assert await manager.initialize_connection("user-1", "websocket", "conn-1") is False
            assert ChannelRegistry._instances == {}
        finally:
            _reset_failures()


class TestStopDuringInitialization:
    @pytest.mark.asyncio
    async def test_late_finishing_handler_is_torn_down(self, manager, monkeypatch):
        """A stop arriving mid-initialization must not leave a live handler."""
        _reset_failures()
        started = asyncio.Event()
        release = asyncio.Event()
        original_start = _FailingHandler.start

        async def _slow_start(self, context):
            started.set()
            await release.wait()
            return await original_start(self, context)

        monkeypatch.setattr(_FailingHandler, "start", _slow_start)

        init_task = asyncio.create_task(
            manager.initialize_connection("user-1", "websocket", "conn-1")
        )
        await started.wait()

        # Stop while the handler is still starting up.
        await manager.stop_connection("user-1", "websocket", "conn-1")

        release.set()
        ok = await init_task

        key = ChannelManager._instance_key("user-1", "websocket", "conn-1")
        assert ok is False
        assert key not in manager._active_connections
        assert ChannelRegistry.get_instance(key) is None
        assert key not in manager._stop_intents or key not in manager._active_connections

    @pytest.mark.asyncio
    async def test_successful_initialization_registers_handler(self, manager):
        _reset_failures()
        assert await manager.initialize_connection("user-1", "websocket", "conn-1") is True
        key = ChannelManager._instance_key("user-1", "websocket", "conn-1")
        assert manager._active_connections[key] is not None
        assert ChannelRegistry.get_instance(key) is not None
        # Lookups resolve through the recorded coordinates.
        assert manager.get_user_connections("user-1")[0]["id"] == "conn-1"
        assert manager.find_active_connection("websocket", "conn-1") is not None
        assert manager.get_connection_runtime_status("conn-1") == "connected"

        await manager.stop_connection("user-1", "websocket", "conn-1")
        assert ChannelRegistry.get_instance(key) is None
