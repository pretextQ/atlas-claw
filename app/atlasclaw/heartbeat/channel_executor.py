# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.atlasclaw.heartbeat.events import build_heartbeat_event_payload
from app.atlasclaw.heartbeat.models import HeartbeatJobDefinition


ChannelProbeCallable = Callable[[HeartbeatJobDefinition], Awaitable[dict[str, Any]]]


@dataclass
class ChannelHeartbeatExecutionResult:
    """Structured result for a single channel heartbeat check."""

    status: str
    result_summary: str
    should_alert: bool
    consecutive_failures: int
    context_payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    extra_event_types: list[str] = field(default_factory=list)
    next_delay_seconds: int = 0


class ChannelHeartbeatExecutor:
    """Execute channel connection heartbeat checks with quiet retry semantics."""

    def __init__(
        self,
        channel_probe: ChannelProbeCallable,
        failure_threshold: int = 3,
        degraded_threshold: int = 3,
        reconnect_backoff_seconds: list[int] | None = None,
    ):
        self.channel_probe = channel_probe
        self.failure_threshold = max(1, failure_threshold)
        self.degraded_threshold = max(1, degraded_threshold)
        self.reconnect_backoff_seconds = list(reconnect_backoff_seconds or [10, 30, 60, 300])
        self._failures_by_job: dict[str, int] = {}

    async def execute(self, job: HeartbeatJobDefinition) -> ChannelHeartbeatExecutionResult:
        try:
            probe = await self.channel_probe(job)
        except Exception as exc:  # noqa: BLE001
            return self._build_failure(job, summary="probe_exception", error=str(exc))

        healthy = bool(probe.get("healthy", False))
        if healthy:
            self._failures_by_job[job.job_id] = 0
            payload = build_heartbeat_event_payload(
                job_id=job.job_id,
                job_type=job.job_type.value,
                result_summary=str(probe.get("summary", "healthy")),
                status="healthy",
                metadata=dict(probe),
            )
            return ChannelHeartbeatExecutionResult(
                status="healthy",
                result_summary=str(probe.get("summary", "healthy")),
                should_alert=False,
                consecutive_failures=0,
                context_payload=payload,
                extra_event_types=(
                    ["heartbeat.channel.reconnect_succeeded"]
                    if probe.get("reconnected")
                    else []
                ),
                next_delay_seconds=max(1, job.every_seconds or 30),
            )

        extra_events: list[str] = []
        if probe.get("reconnect_attempted"):
            extra_events.append("heartbeat.channel.reconnect_started")
            if not probe.get("reconnected"):
                extra_events.append("heartbeat.channel.reconnect_failed")

        return self._build_failure(
            job,
            summary=str(probe.get("summary", "connection_failed")),
            error=str(probe.get("status", "")),
            metadata=dict(probe),
            extra_event_types=extra_events,
        )

    def _build_failure(
        self,
        job: HeartbeatJobDefinition,
        *,
        summary: str,
        error: str,
        metadata: dict[str, Any] | None = None,
        extra_event_types: list[str] | None = None,
    ) -> ChannelHeartbeatExecutionResult:
        failures = self._failures_by_job.get(job.job_id, 0) + 1
        self._failures_by_job[job.job_id] = failures
        degraded = failures >= self.degraded_threshold
        status = "degraded" if degraded else "failed"
        payload = build_heartbeat_event_payload(
            job_id=job.job_id,
            job_type=job.job_type.value,
            result_summary=summary,
            status=status,
            error=error,
            metadata=dict(metadata or {}),
        )
        return ChannelHeartbeatExecutionResult(
            status=status,
            result_summary=summary,
            should_alert=degraded,
            consecutive_failures=failures,
            context_payload=payload,
            error=error,
            extra_event_types=list(extra_event_types or []),
            next_delay_seconds=self._next_backoff_delay(failures),
        )

    def _next_backoff_delay(self, failures: int) -> int:
        if not self.reconnect_backoff_seconds:
            return 30
        index = max(0, min(failures - 1, len(self.reconnect_backoff_seconds) - 1))
        return max(1, int(self.reconnect_backoff_seconds[index]))
