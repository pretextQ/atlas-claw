# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for channel models and registry."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.atlasclaw.channels import (
    ChannelConnection,
    ChannelMode,
    ChannelRegistry,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    MessageAcknowledgementResult,
    OutboundMessage,
    SendResult,
)
from app.atlasclaw.channels.handler import ChannelHandler
from tests.atlasclaw.channel_test_stubs import StubChannelHandler


class TestChannelModels:
    """Test channel data models."""

    def test_channel_mode_enum(self):
        """Test ChannelMode enum values."""
        assert ChannelMode.INBOUND.value == "inbound"
        assert ChannelMode.OUTBOUND.value == "outbound"
        assert ChannelMode.BIDIRECTIONAL.value == "bidirectional"

    def test_connection_status_enum(self):
        """Test ConnectionStatus enum."""
        assert ConnectionStatus.DISCONNECTED.name == "DISCONNECTED"
        assert ConnectionStatus.CONNECTED.name == "CONNECTED"

    def test_inbound_message_creation(self):
        """Test InboundMessage dataclass."""
        msg = InboundMessage(
            message_id="msg-123",
            sender_id="user-456",
            sender_name="Test User",
            chat_id="chat-789",
            channel_type="test",
            content="Hello",
        )
        assert msg.message_id == "msg-123"
        assert msg.content == "Hello"
        assert msg.content_type == "text"  # default value

    def test_outbound_message_creation(self):
        """Test OutboundMessage dataclass."""
        msg = OutboundMessage(
            chat_id="chat-789",
            content="Reply",
            content_type="markdown",
        )
        assert msg.chat_id == "chat-789"
        assert msg.content == "Reply"
        assert msg.content_type == "markdown"

    def test_send_result(self):
        """Test SendResult dataclass."""
        result = SendResult(success=True, message_id="msg-123")
        assert result.success is True
        assert result.message_id == "msg-123"

    def test_message_acknowledgement_result(self):
        """Test MessageAcknowledgementResult dataclass."""
        result = MessageAcknowledgementResult(
            supported=True,
            success=True,
            metadata={"stream_id": "stream-123"},
        )

        assert result.supported is True
        assert result.success is True
        assert result.metadata["stream_id"] == "stream-123"

    def test_channel_connection(self):
        """Test ChannelConnection dataclass."""
        conn = ChannelConnection(
            id="conn-123",
            name="Test Connection",
            channel_type="websocket",
            config={"host": "localhost"},
            enabled=True,
        )
        assert conn.id == "conn-123"
        assert conn.channel_type == "websocket"
        assert conn.config["host"] == "localhost"


class TestChannelRegistry:
    """Test ChannelRegistry functionality."""

    def setup_method(self):
        """Clear registry before each test."""
        ChannelRegistry._handlers.clear()
        ChannelRegistry._instances.clear()
        ChannelRegistry._connections.clear()

    def test_register_handler(self):
        """Test registering a channel handler."""
        ChannelRegistry.register("websocket", StubChannelHandler)
        
        assert "websocket" in ChannelRegistry._handlers
        assert ChannelRegistry._handlers["websocket"] == StubChannelHandler

    def test_get_handler(self):
        """Test getting a registered handler."""
        ChannelRegistry.register("websocket", StubChannelHandler)
        
        handler_class = ChannelRegistry.get("websocket")
        assert handler_class == StubChannelHandler

    def test_get_nonexistent_handler(self):
        """Test getting a non-existent handler."""
        handler_class = ChannelRegistry.get("nonexistent")
        assert handler_class is None

    def test_list_channels(self):
        """Test listing registered channels."""
        ChannelRegistry.register("websocket", StubChannelHandler)
        ChannelRegistry.register("sse", StubChannelHandler)
        
        channels = ChannelRegistry.list_channels()
        assert len(channels) == 2
        
        types = [c["type"] for c in channels]
        assert "websocket" in types
        assert "sse" in types

    def test_create_instance(self):
        """Test creating handler instance."""
        ChannelRegistry.register("websocket", StubChannelHandler)
        
        instance = ChannelRegistry.create_instance(
            "instance-1",
            "websocket",
            {"path": "/ws"}
        )
        
        assert instance is not None
        assert isinstance(instance, StubChannelHandler)
        assert instance.config["path"] == "/ws"

    def test_get_instance(self):
        """Test getting cached instance."""
        ChannelRegistry.register("websocket", StubChannelHandler)
        ChannelRegistry.create_instance("instance-1", "websocket", {})
        
        instance = ChannelRegistry.get_instance("instance-1")
        assert instance is not None
        assert isinstance(instance, StubChannelHandler)

    def test_register_connection(self):
        """Test registering a channel connection."""
        conn = ChannelConnection(
            id="conn-123",
            name="Test",
            channel_type="websocket",
        )
        
        ChannelRegistry.register_connection(conn)
        
        retrieved = ChannelRegistry.get_connection("conn-123")
        assert retrieved == conn
