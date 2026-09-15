# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.heartbeat.models import HeartbeatTargetDescriptor, HeartbeatTargetType
from app.atlasclaw.heartbeat.targets import HeartbeatTargetResolver


def test_group_chat_target_resolves_summary_only() -> None:
    resolver = HeartbeatTargetResolver()
    target = resolver.resolve(
        HeartbeatTargetDescriptor(
            type=HeartbeatTargetType.GROUP_CHAT,
            user_id="admin",
            channel="feishu",
            account_id="conn-1",
            peer_id="chat-1",
        )
    )

    assert target.delivery_mode == "summary_only"
    assert target.channel == "feishu"
    assert target.peer_id == "chat-1"


def test_last_active_target_uses_metadata_fallbacks() -> None:
    resolver = HeartbeatTargetResolver()
    target = resolver.resolve(
        HeartbeatTargetDescriptor(
            type=HeartbeatTargetType.LAST_ACTIVE,
            user_id="",
            metadata={
                "user_id": "admin",
                "channel": "web",
                "session_key": "agent:main:user:admin:web:dm:admin",
            },
        )
    )

    assert target.user_id == "admin"
    assert target.channel == "web"
    assert target.session_key == "agent:main:user:admin:web:dm:admin"
