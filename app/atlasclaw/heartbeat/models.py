# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def _parse_datetime(value: Optional[str | datetime]) -> Optional[datetime]:
    """Normalize ISO strings or datetimes into timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class HeartbeatJobType(str, Enum):
    """Supported heartbeat job types."""

    AGENT_TURN = "agent_turn"
    CHANNEL_CONNECTION = "channel_connection"


class HeartbeatTargetType(str, Enum):
    """Supported heartbeat delivery targets."""

    NONE = "none"
    LAST_ACTIVE = "last_active"
    USER_CHAT = "user_chat"
    CHANNEL_CONNECTION = "channel_connection"
    GROUP_CHAT = "group_chat"
    SESSION = "session"
    THREAD = "thread"


class HeartbeatDeliveryMode(str, Enum):
    """Supported outbound delivery modes for resolved heartbeat targets."""

    SILENT = "silent"
    DIRECT = "direct"
    SUMMARY_ONLY = "summary_only"


class HeartbeatEventType(str, Enum):
    """Canonical heartbeat runtime events."""

    AGENT_STARTED = "heartbeat.agent.started"
    AGENT_COMPLETED = "heartbeat.agent.completed"
    AGENT_FAILED = "heartbeat.agent.failed"
    CHANNEL_CHECK_STARTED = "heartbeat.channel.check_started"
    CHANNEL_CHECK_SUCCEEDED = "heartbeat.channel.check_succeeded"
    CHANNEL_CHECK_FAILED = "heartbeat.channel.check_failed"
    CHANNEL_RECONNECT_STARTED = "heartbeat.channel.reconnect_started"
    CHANNEL_RECONNECT_SUCCEEDED = "heartbeat.channel.reconnect_succeeded"
    CHANNEL_RECONNECT_FAILED = "heartbeat.channel.reconnect_failed"
    CHANNEL_DEGRADED = "heartbeat.channel.degraded"


@dataclass
class HeartbeatTargetDescriptor:
    """Resolved heartbeat target descriptor."""

    type: HeartbeatTargetType
    user_id: str = ""
    channel: str = ""
    account_id: str = ""
    peer_id: str = ""
    session_key: str = ""
    thread_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "user_id": self.user_id,
            "channel": self.channel,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatTargetDescriptor":
        return cls(
            type=HeartbeatTargetType(data["type"]),
            user_id=data.get("user_id", ""),
            channel=data.get("channel", ""),
            account_id=data.get("account_id", ""),
            peer_id=data.get("peer_id", ""),
            session_key=data.get("session_key", ""),
            thread_id=data.get("thread_id", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResolvedHeartbeatTarget:
    """Concrete target after runtime resolution."""

    type: HeartbeatTargetType
    user_id: str = ""
    channel: str = ""
    account_id: str = ""
    peer_id: str = ""
    session_key: str = ""
    thread_id: str = ""
    delivery_mode: HeartbeatDeliveryMode = HeartbeatDeliveryMode.SILENT
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "user_id": self.user_id,
            "channel": self.channel,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "delivery_mode": self.delivery_mode.value,
            "metadata": dict(self.metadata),
        }


@dataclass
class HeartbeatJobDefinition:
    """Configured heartbeat job definition."""

    job_id: str
    job_type: HeartbeatJobType
    owner_user_id: str
    enabled: bool = True
    every_seconds: int = 0
    target: Optional[HeartbeatTargetDescriptor] = None
    active_hours_timezone: str = ""
    active_hours_start: str = ""
    active_hours_end: str = ""
    isolated_session: bool = False
    light_context: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "owner_user_id": self.owner_user_id,
            "enabled": self.enabled,
            "every_seconds": self.every_seconds,
            "target": self.target.to_dict() if self.target else None,
            "active_hours_timezone": self.active_hours_timezone,
            "active_hours_start": self.active_hours_start,
            "active_hours_end": self.active_hours_end,
            "isolated_session": self.isolated_session,
            "light_context": self.light_context,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatJobDefinition":
        target = data.get("target")
        return cls(
            job_id=data["job_id"],
            job_type=HeartbeatJobType(data["job_type"]),
            owner_user_id=data["owner_user_id"],
            enabled=bool(data.get("enabled", True)),
            every_seconds=int(data.get("every_seconds", 0)),
            target=HeartbeatTargetDescriptor.from_dict(target) if isinstance(target, dict) else None,
            active_hours_timezone=data.get("active_hours_timezone", ""),
            active_hours_start=data.get("active_hours_start", ""),
            active_hours_end=data.get("active_hours_end", ""),
            isolated_session=bool(data.get("isolated_session", False)),
            light_context=bool(data.get("light_context", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class HeartbeatJobStateSnapshot:
    """Persisted state snapshot for a heartbeat job."""

    job_id: str
    job_type: HeartbeatJobType
    status: str
    consecutive_failures: int = 0
    last_error: str = ""
    last_result_summary: str = ""
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_target_resolution: dict[str, Any] = field(default_factory=dict)
    last_delivery_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_result_summary": self.last_result_summary,
            "last_run_at": self.last_run_at.astimezone(timezone.utc).isoformat() if self.last_run_at else None,
            "last_success_at": self.last_success_at.astimezone(timezone.utc).isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.astimezone(timezone.utc).isoformat() if self.last_failure_at else None,
            "next_run_at": self.next_run_at.astimezone(timezone.utc).isoformat() if self.next_run_at else None,
            "last_target_resolution": dict(self.last_target_resolution),
            "last_delivery_result": dict(self.last_delivery_result),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatJobStateSnapshot":
        return cls(
            job_id=data["job_id"],
            job_type=HeartbeatJobType(data["job_type"]),
            status=data.get("status", "idle"),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            last_error=data.get("last_error", ""),
            last_result_summary=data.get("last_result_summary", ""),
            last_run_at=_parse_datetime(data.get("last_run_at")),
            last_success_at=_parse_datetime(data.get("last_success_at")),
            last_failure_at=_parse_datetime(data.get("last_failure_at")),
            next_run_at=_parse_datetime(data.get("next_run_at")),
            last_target_resolution=dict(data.get("last_target_resolution", {})),
            last_delivery_result=dict(data.get("last_delivery_result", {})),
        )


@dataclass
class HeartbeatEventEnvelope:
    """Heartbeat runtime event envelope."""

    id: str
    event_type: HeartbeatEventType
    job_id: str
    job_type: HeartbeatJobType
    user_id: str
    created_at: datetime
    channel: str = ""
    account_id: str = ""
    session_key: str = ""
    run_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "user_id": self.user_id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "channel": self.channel,
            "account_id": self.account_id,
            "session_key": self.session_key,
            "run_id": self.run_id,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatEventEnvelope":
        return cls(
            id=data["id"],
            event_type=HeartbeatEventType(data["event_type"]),
            job_id=data["job_id"],
            job_type=HeartbeatJobType(data["job_type"]),
            user_id=data["user_id"],
            created_at=_parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            channel=data.get("channel", ""),
            account_id=data.get("account_id", ""),
            session_key=data.get("session_key", ""),
            run_id=data.get("run_id", ""),
            payload=dict(data.get("payload", {})),
        )
