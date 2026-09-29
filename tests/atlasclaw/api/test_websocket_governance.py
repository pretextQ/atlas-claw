# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
WebSocket 连接治理回归测试（WP-01）

对应复测脚本 checks/wp01_standalone_check.py 的 11 项检查：
强制认证（4401）、身份采信、PONG 保活、ping 任务异常回收、
坏帧计数断开、异常信息脱敏、幂等缓存与订阅集合清理。
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import WebSocketDisconnect
from unittest.mock import patch

from app.atlasclaw.api.websocket import ConnectionInfo, WebSocketManager
from app.atlasclaw.auth.models import UserInfo

_WS_LOGGER = "app.atlasclaw.api.websocket"


class _DisconnectSentinel:
    pass


_DISCONNECT = _DisconnectSentinel()


class FakeWebSocket:
    """In-memory WebSocket double: queued incoming frames, recorded outgoing frames.

    Server pings are answered with pong automatically; queued exceptions model
    undecodable frames, ``disconnect_after`` models the peer hanging up.
    """

    def __init__(self, incoming=None, disconnect_after=None):
        self.incoming = list(incoming or [])
        self.disconnect_after = disconnect_after
        self.sent = []
        self.closed = []
        self._received = 0

    async def accept(self):
        return None

    async def send_json(self, data):
        self.sent.append(data)
        if isinstance(data, dict) and data.get("type") == "ping":
            self.incoming.append({"type": "pong"})

    async def receive_json(self):
        while not self.incoming:
            await asyncio.sleep(0)
        self._received += 1
        if self.disconnect_after is not None and self._received > self.disconnect_after:
            raise WebSocketDisconnect()
        item = self.incoming.pop(0)
        if isinstance(item, _DisconnectSentinel):
            raise WebSocketDisconnect()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))


async def _run_connection(manager: WebSocketManager, ws: FakeWebSocket) -> None:
    await asyncio.wait_for(manager.handle_connection(ws), timeout=2.0)


def _auth_user(user_id: str = "u-authenticated") -> UserInfo:
    return UserInfo(user_id=user_id, display_name="Test User")


def _record_text(record) -> str:
    return record.getMessage() + " | " + str(record.exc_text or "")


class TestWebSocketAuthEnforcement:
    """An auth handler must mean mandatory authentication (WP-01 F-0002)."""

    @pytest.mark.asyncio
    async def test_missing_token_rejected_with_4401(self):
        async def handler(token):
            return _auth_user("u-never")

        manager = WebSocketManager(auth_handler=handler)
        ws = FakeWebSocket(incoming=[{"type": "connect", "device_id": "d"}])
        await _run_connection(manager, ws)
        assert ws.closed == [(4401, "Missing auth token")], ws.closed
        assert manager.get_connection_count() == 0
        assert not any(f.get("type") == "hello-ok" for f in ws.sent)

    @pytest.mark.asyncio
    async def test_empty_token_rejected_without_calling_handler(self):
        called = []

        async def handler(token):
            called.append(token)
            return _auth_user("u-never")

        manager = WebSocketManager(auth_handler=handler)
        ws = FakeWebSocket(incoming=[{"type": "connect", "auth_token": ""}])
        await _run_connection(manager, ws)
        assert ws.closed == [(4401, "Missing auth token")], ws.closed
        assert not called

    @pytest.mark.asyncio
    async def test_forged_user_id_not_trusted_when_authenticated(self):
        ui = _auth_user("u-authenticated")

        async def handler(token):
            return ui if token == "valid-token" else None

        manager = WebSocketManager(auth_handler=handler)
        ws = FakeWebSocket(incoming=[{
            "type": "connect", "auth_token": "valid-token", "user_id": "u-admin-forged",
        }])

        captured = {}

        async def capture_loop(conn_id, ws, conn_info):
            captured["conn"] = conn_info
            raise WebSocketDisconnect()

        with patch.object(manager, "_message_loop", side_effect=capture_loop):
            await _run_connection(manager, ws)
        assert captured["conn"].user_id == "u-authenticated"
        assert captured["conn"].user_info is ui

    @pytest.mark.asyncio
    async def test_anonymous_connection_ignores_client_user_id(self):
        manager = WebSocketManager()
        ws = FakeWebSocket(incoming=[{"type": "connect", "user_id": "u-claimed"}])

        captured = {}

        async def capture_loop(conn_id, ws, conn_info):
            captured["conn"] = conn_info
            raise WebSocketDisconnect()

        with patch.object(manager, "_message_loop", side_effect=capture_loop):
            await _run_connection(manager, ws)
        assert captured["conn"].user_id == ""


class TestWebSocketKeepalive:
    @pytest.mark.asyncio
    async def test_pong_keeps_idle_client_alive(self):
        """A client that answers every ping must not be force-disconnected."""
        manager = WebSocketManager(ping_interval=0.05, ping_timeout=0.05)
        ws = FakeWebSocket(incoming=[{"type": "connect"}], disconnect_after=8)
        await _run_connection(manager, ws)
        assert any(f.get("type") == "ping" for f in ws.sent), "server should ping"
        assert (4003, "Ping timeout") not in ws.closed, ws.closed

    @pytest.mark.asyncio
    async def test_ping_loop_failure_is_retrieved_and_logged(self, caplog):
        """A crashed ping loop must be awaited and logged, never orphaned."""
        manager = WebSocketManager()
        started = asyncio.Event()

        async def boom(connection_id):
            started.set()
            raise RuntimeError("ping loop exploded")

        ws = FakeWebSocket(incoming=[{"type": "connect"}])

        async def queue_disconnect():
            await started.wait()
            ws.incoming.append(_DISCONNECT)

        task = asyncio.create_task(queue_disconnect())
        with caplog.at_level(logging.ERROR, logger=_WS_LOGGER):
            with patch.object(manager, "_ping_loop", side_effect=boom):
                await _run_connection(manager, ws)
        await task
        assert any("ping loop exploded" in _record_text(r) for r in caplog.records), caplog.records


class TestWebSocketProtocolErrors:
    @pytest.mark.asyncio
    async def test_bad_frames_counted_and_connection_closed_4008(self):
        manager = WebSocketManager()
        manager._max_protocol_errors = 3
        ws = FakeWebSocket(incoming=[
            {"type": "connect"},
            ValueError("bad1"), ValueError("bad2"), ValueError("bad3"),
        ])
        await _run_connection(manager, ws)
        assert (4008, "Too many protocol errors") in ws.closed, ws.closed
        error_frames = [f for f in ws.sent if f.get("type") == "error"]
        assert len(error_frames) == 3
        assert all(f["error"] == "Internal error" for f in error_frames), error_frames


class TestWebSocketErrorRedaction:
    @pytest.mark.asyncio
    async def test_handler_exception_not_echoed_to_client(self):
        manager = WebSocketManager()

        async def boom(conn_info, **params):
            raise RuntimeError("internal db password=hunter2")

        manager.register_handler("boom", boom)
        ws = FakeWebSocket()
        await manager._handle_request(
            ws, ConnectionInfo(connection_id="c1", user_id="u"), {"id": "r1", "method": "boom"}
        )
        assert ws.sent[-1]["error"] == "Internal error", ws.sent[-1]

    @pytest.mark.asyncio
    async def test_close_reason_does_not_leak_exception_text(self, caplog):
        manager = WebSocketManager()
        ws = FakeWebSocket(incoming=[{"type": "connect"}])

        async def explode(conn_id, ws, conn_info):
            raise RuntimeError("secret-path D:/db/creds.txt")

        with caplog.at_level(logging.ERROR, logger=_WS_LOGGER):
            with patch.object(manager, "_message_loop", side_effect=explode):
                await _run_connection(manager, ws)
        assert (4000, "Internal error") in ws.closed, ws.closed
        assert all("secret-path" not in reason for _, reason in ws.closed)
        assert any("secret-path" in _record_text(r) for r in caplog.records)


class TestWebSocketCleanup:
    @pytest.mark.asyncio
    async def test_idempotency_cache_cleared_after_disconnect(self):
        manager = WebSocketManager()

        async def echo(conn_info, message):
            return {"echo": message}

        manager.register_handler("echo", echo)
        ws = FakeWebSocket(incoming=[
            {"type": "connect"},
            {"type": "req", "id": "r1", "method": "echo",
             "params": {"message": "hi"}, "idempotency_key": "k1"},
            _DISCONNECT,
        ])
        await _run_connection(manager, ws)
        assert manager._idempotency_cache == {}, manager._idempotency_cache

    def test_empty_subscriber_set_removed_after_cleanup(self):
        manager = WebSocketManager()
        manager._connections["conn-1"] = (None, ConnectionInfo(connection_id="conn-1"))
        assert manager.subscribe_session("conn-1", "session-a")
        manager._cleanup_connection("conn-1")
        assert "session-a" not in manager._session_subscribers, manager._session_subscribers
