# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Shared channel-handler test doubles.

The built-in WebSocket/SSE/REST channel handlers were removed as dead code
(no registry entry referenced them), but registry, manager, and API tests
still need a concrete ``ChannelHandler``. ``StubChannelHandler`` provides the
minimal lifecycle those tests exercise.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.atlasclaw.channels.handler import ChannelHandler
from app.atlasclaw.channels.models import (
    ChannelMode,
    ChannelValidationResult,
    ConnectionStatus,
    InboundMessage,
    SendResult,
)

logger = logging.getLogger(__name__)


class StubChannelHandler(ChannelHandler):
    """Minimal bidirectional handler used by channel tests."""

    channel_type = "stub"
    channel_name = "Stub"
    channel_icon = ""
    channel_mode = ChannelMode.BIDIRECTIONAL
    supports_long_connection = True
    supports_webhook = False

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.sent_messages: list[OutboundMessage] = []

    async def setup(self, connection_config: Dict[str, Any]) -> bool:
        self.config.update(connection_config)
        return True

    async def start(self, context: Any) -> bool:
        self._status = ConnectionStatus.CONNECTED
        return True

    async def stop(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        return True

    async def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTED
        return True

    async def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        return True

    async def handle_inbound(self, request: Any) -> Optional[InboundMessage]:
        """Parse a JSON message payload into an InboundMessage."""
        try:
            if isinstance(request, str):
                data = json.loads(request)
            else:
                data = request

            return InboundMessage(
                message_id=data.get("message_id", ""),
                sender_id=data.get("sender_id", ""),
                sender_name=data.get("sender_name", "Anonymous"),
                chat_id=data.get("chat_id", data.get("sender_id", "")),
                channel_type=self.channel_type,
                content=data.get("content", ""),
                content_type=data.get("content_type", "text"),
                thread_id=data.get("thread_id"),
                reply_to=data.get("reply_to"),
                metadata=data.get("metadata", {}),
            )
        except Exception as e:
            logger.error(f"Stub handler failed to parse message: {e}")
            return None

    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        self.sent_messages.append(outbound)
        return SendResult(success=True)

    async def validate_config(self, config: Dict[str, Any]) -> ChannelValidationResult:
        if not isinstance(config, dict):
            return ChannelValidationResult(valid=False, errors=["Config must be a dictionary"])
        return ChannelValidationResult(valid=True)

    def describe_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Channel path"}},
            "required": [],
        }
