# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for channel webhook routes."""

from __future__ import annotations

import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api import channels as channels_api
from app.atlasclaw.api.channel_hooks import router as channel_hooks_router
from app.atlasclaw.api.deps_context import APIContext
from app.atlasclaw.channels import ChannelRegistry
from tests.atlasclaw.channel_test_stubs import StubChannelHandler
from app.atlasclaw.channels.manager import ChannelManager
from app.atlasclaw.channels.models import SendResult
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


class RecordingAgentRunner:
    """Minimal agent runner that records user messages and replies once."""

    def __init__(self):
        self.messages = []

    async def run(self, **kwargs):
        self.messages.append(kwargs.get("user_message", ""))
        yield SimpleNamespace(type="assistant", content="ok")


@pytest.fixture
def channel_env():
    """FastAPI app with a real ChannelManager wiring one active connection."""
    app = FastAPI()
    app.include_router(channel_hooks_router)

    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry.register("websocket", StubChannelHandler)

    workspace = tempfile.mkdtemp()
    manager = ChannelManager(workspace)
    runner = RecordingAgentRunner()
    manager.set_agent_runner(runner)
    handler = StubChannelHandler({})
    handler.send_message = AsyncMock(return_value=SendResult(success=True))
    manager._active_connections["user-1:websocket:conn-123"] = handler

    ctx = APIContext(
        session_manager=SessionManager(workspace),
        session_queue=SessionQueue(),
        skill_registry=SkillRegistry(),
        provider_instances={},
    )

    previous_manager = channels_api._channel_manager
    channels_api._channel_manager = manager
    try:
        with patch("app.atlasclaw.api.deps_context.get_api_context", return_value=ctx):
            yield SimpleNamespace(client=TestClient(app), manager=manager, runner=runner, handler=handler)
    finally:
        channels_api._channel_manager = previous_manager


def _wait_for(predicate, timeout_seconds: float = 5.0) -> bool:
    """Poll a predicate; background tasks run on the portal loop thread."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestChannelHooks:
    """Test channel webhook routes."""

    def test_receive_webhook_channel_not_found(self, channel_env):
        """Webhook with a non-existent channel type returns 404."""
        response = channel_env.client.post(
            "/api/channel-hooks/nonexistent/conn-123",
            json={"message_id": "msg-123", "content": "Hello"}
        )

        assert response.status_code == 404
        assert "Channel type not found" in response.json()["detail"]

    def test_receive_webhook_connection_not_active(self, channel_env):
        """Webhook for a connection without an active handler returns 404."""
        response = channel_env.client.post(
            "/api/channel-hooks/websocket/nonexistent",
            json={"message_id": "msg-123", "content": "Hello"}
        )

        assert response.status_code == 404
        assert "Connection not found or not active" in response.json()["detail"]

    def test_receive_webhook_routes_message_to_agent(self, channel_env):
        """A valid webhook message is routed to the agent and replied to."""
        response = channel_env.client.post(
            "/api/channel-hooks/websocket/conn-123",
            json={
                "message_id": "msg-123",
                "sender_id": "user-456",
                "sender_name": "Test User",
                "chat_id": "chat-789",
                "content": "Hello"
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "message_id": "msg-123"}

        assert _wait_for(lambda: bool(channel_env.runner.messages)), "agent never received the message"
        assert channel_env.runner.messages == ["Hello"]
        channel_env.handler.send_message.assert_awaited_once()

    def test_receive_webhook_routes_raw_event_body(self, channel_env):
        """Handlers receive the raw platform payload, not an HTTP wrapper."""
        captured = {}

        async def _capture_handle_inbound(request):
            captured["request"] = request
            return None

        channel_env.handler.handle_inbound = AsyncMock(side_effect=_capture_handle_inbound)

        channel_env.client.post(
            "/api/channel-hooks/websocket/conn-123",
            json={"message_id": "msg-123", "content": "Hello"}
        )

        assert captured["request"] == {"message_id": "msg-123", "content": "Hello"}

    def test_receive_webhook_invalid_json_body(self, channel_env):
        """Malformed JSON bodies are rejected with 400."""
        response = channel_env.client.post(
            "/api/channel-hooks/websocket/conn-123",
            content=b"{not json",
            headers={"content-type": "application/json"}
        )

        assert response.status_code == 400

    def test_receive_webhook_unparseable_payload(self, channel_env):
        """Payloads the handler cannot parse return 400."""
        response = channel_env.client.post(
            "/api/channel-hooks/websocket/conn-123",
            data="invalid json",
            headers={"content-type": "text/plain"}
        )

        assert response.status_code == 400
        assert "Invalid message format" in response.json()["detail"]

    def test_verify_webhook_challenge(self, channel_env):
        """Webhook verification with challenge echoes the challenge."""
        response = channel_env.client.get(
            "/api/channel-hooks/websocket/conn-123",
            params={"challenge": "test-challenge-123"}
        )

        assert response.status_code == 200
        assert response.json()["challenge"] == "test-challenge-123"

    def test_verify_webhook_no_challenge(self, channel_env):
        """Webhook verification without challenge returns ok."""
        response = channel_env.client.get("/api/channel-hooks/websocket/conn-123")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
