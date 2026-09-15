# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from typing import Any, Optional

from app.atlasclaw.heartbeat.models import HeartbeatEventEnvelope, HeartbeatEventType
from app.atlasclaw.hooks.runtime import HookRuntime
from app.atlasclaw.hooks.runtime_models import HookEventType


def build_heartbeat_event_payload(
    *,
    job_id: str,
    job_type: str,
    result_summary: str,
    status: str,
    error: str = "",
    target: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a normalized heartbeat event payload for downstream consumers."""

    return {
        "job_id": job_id,
        "job_type": job_type,
        "result_summary": result_summary,
        "status": status,
        "error": error,
        "target": dict(target or {}),
        "metadata": dict(metadata or {}),
    }


def heartbeat_event_to_hook_event_type(event_type: HeartbeatEventType) -> HookEventType:
    """Map heartbeat runtime events onto hook runtime event types."""

    mapping = {
        HeartbeatEventType.AGENT_STARTED: HookEventType.HEARTBEAT_AGENT_STARTED,
        HeartbeatEventType.AGENT_COMPLETED: HookEventType.HEARTBEAT_AGENT_COMPLETED,
        HeartbeatEventType.AGENT_FAILED: HookEventType.HEARTBEAT_AGENT_FAILED,
        HeartbeatEventType.CHANNEL_CHECK_STARTED: HookEventType.HEARTBEAT_CHANNEL_CHECK_STARTED,
        HeartbeatEventType.CHANNEL_CHECK_SUCCEEDED: HookEventType.HEARTBEAT_CHANNEL_CHECK_SUCCEEDED,
        HeartbeatEventType.CHANNEL_CHECK_FAILED: HookEventType.HEARTBEAT_CHANNEL_CHECK_FAILED,
        HeartbeatEventType.CHANNEL_RECONNECT_STARTED: HookEventType.HEARTBEAT_CHANNEL_RECONNECT_STARTED,
        HeartbeatEventType.CHANNEL_RECONNECT_SUCCEEDED: HookEventType.HEARTBEAT_CHANNEL_RECONNECT_SUCCEEDED,
        HeartbeatEventType.CHANNEL_RECONNECT_FAILED: HookEventType.HEARTBEAT_CHANNEL_RECONNECT_FAILED,
        HeartbeatEventType.CHANNEL_DEGRADED: HookEventType.HEARTBEAT_CHANNEL_DEGRADED,
    }
    return mapping[event_type]


def heartbeat_envelope_to_hook_payload(event: HeartbeatEventEnvelope) -> dict[str, Any]:
    """Build a generic hook payload from a heartbeat runtime event envelope."""

    return {
        "heartbeat_event_id": event.id,
        "job_id": event.job_id,
        "job_type": event.job_type.value,
        "channel": event.channel,
        "account_id": event.account_id,
        "session_key": event.session_key,
        "run_id": event.run_id,
        "payload": dict(event.payload),
    }


async def emit_heartbeat_event_to_hook_runtime(
    hook_runtime: HookRuntime,
    event: HeartbeatEventEnvelope,
) -> None:
    """Bridge a heartbeat runtime event into the generic hook runtime."""

    await hook_runtime.emit(
        event_type=heartbeat_event_to_hook_event_type(event.event_type),
        user_id=event.user_id,
        session_key=event.session_key,
        run_id=event.run_id or event.job_id,
        channel=event.channel or "heartbeat",
        agent_id="heartbeat",
        payload=heartbeat_envelope_to_hook_payload(event),
    )
