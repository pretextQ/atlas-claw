# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.heartbeat.models import (
    HeartbeatDeliveryMode,
    HeartbeatTargetDescriptor,
    HeartbeatTargetType,
    ResolvedHeartbeatTarget,
)


class HeartbeatTargetResolver:
    """Resolve heartbeat target descriptors into concrete delivery semantics."""

    def resolve(self, descriptor: HeartbeatTargetDescriptor) -> ResolvedHeartbeatTarget:
        if descriptor.type == HeartbeatTargetType.LAST_ACTIVE:
            metadata = dict(descriptor.metadata)
            return ResolvedHeartbeatTarget(
                type=descriptor.type,
                user_id=descriptor.user_id or str(metadata.get("user_id", "")),
                channel=descriptor.channel or str(metadata.get("channel", "web")),
                account_id=descriptor.account_id or str(metadata.get("account_id", "")),
                peer_id=descriptor.peer_id or str(metadata.get("peer_id", "")),
                session_key=descriptor.session_key or str(metadata.get("session_key", "")),
                thread_id=descriptor.thread_id or str(metadata.get("thread_id", "")),
                delivery_mode=HeartbeatDeliveryMode.DIRECT,
                metadata=metadata,
            )
        if descriptor.type == HeartbeatTargetType.USER_CHAT and not descriptor.channel:
            return ResolvedHeartbeatTarget(
                type=descriptor.type,
                user_id=descriptor.user_id,
                channel="web",
                account_id=descriptor.account_id,
                peer_id=descriptor.peer_id or descriptor.user_id,
                session_key=descriptor.session_key,
                thread_id=descriptor.thread_id,
                delivery_mode=HeartbeatDeliveryMode.DIRECT,
                metadata=dict(descriptor.metadata),
            )
        delivery_mode = self._default_delivery_mode(descriptor.type)
        return ResolvedHeartbeatTarget(
            type=descriptor.type,
            user_id=descriptor.user_id,
            channel=descriptor.channel,
            account_id=descriptor.account_id,
            peer_id=descriptor.peer_id,
            session_key=descriptor.session_key,
            thread_id=descriptor.thread_id,
            delivery_mode=delivery_mode,
            metadata=dict(descriptor.metadata),
        )

    def _default_delivery_mode(self, target_type: HeartbeatTargetType) -> HeartbeatDeliveryMode:
        if target_type == HeartbeatTargetType.NONE:
            return HeartbeatDeliveryMode.SILENT
        if target_type == HeartbeatTargetType.GROUP_CHAT:
            return HeartbeatDeliveryMode.SUMMARY_ONLY
        return HeartbeatDeliveryMode.DIRECT
