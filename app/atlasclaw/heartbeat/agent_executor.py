# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.atlasclaw.heartbeat.events import build_heartbeat_event_payload
from app.atlasclaw.heartbeat.models import HeartbeatJobDefinition


AgentRunnerCallable = Callable[[HeartbeatJobDefinition], Awaitable[dict[str, Any]]]


@dataclass
class AgentHeartbeatExecutionResult:
    """Structured result for a single agent heartbeat turn."""

    status: str
    result_summary: str
    should_notify: bool
    context_payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class AgentHeartbeatExecutor:
    """Execute an OpenClaw-style agent heartbeat turn."""

    HEARTBEAT_OK = "HEARTBEAT_OK"

    def __init__(self, agent_runner: AgentRunnerCallable):
        self.agent_runner = agent_runner

    async def execute(self, job: HeartbeatJobDefinition) -> AgentHeartbeatExecutionResult:
        try:
            result = await self.agent_runner(job)
        except Exception as exc:  # noqa: BLE001
            return AgentHeartbeatExecutionResult(
                status="failed",
                result_summary="heartbeat_failed",
                should_notify=True,
                context_payload=build_heartbeat_event_payload(
                    job_id=job.job_id,
                    job_type=job.job_type.value,
                    result_summary="heartbeat_failed",
                    status="failed",
                    error=str(exc),
                ),
                error=str(exc),
            )

        assistant_message = str(result.get("assistant_message", "") or "").strip()
        result_summary = assistant_message or self.HEARTBEAT_OK
        should_notify = result_summary != self.HEARTBEAT_OK
        context_payload = build_heartbeat_event_payload(
            job_id=job.job_id,
            job_type=job.job_type.value,
            result_summary=result_summary,
            status="healthy",
            metadata={
                "system_prompt": result.get("system_prompt", ""),
                "message_history": result.get("message_history", []),
                "assistant_message": assistant_message,
                "tool_calls": result.get("tool_calls", []),
                "session_title": result.get("session_title", ""),
                "session_key": result.get("session_key", ""),
                "run_id": result.get("run_id", ""),
            },
        )
        context_payload["assistant_message"] = assistant_message
        context_payload["message_history"] = result.get("message_history", [])
        context_payload["system_prompt"] = result.get("system_prompt", "")
        context_payload["tool_calls"] = result.get("tool_calls", [])
        context_payload["session_title"] = result.get("session_title", "")
        context_payload["session_key"] = result.get("session_key", "")
        context_payload["run_id"] = result.get("run_id", "")

        return AgentHeartbeatExecutionResult(
            status="healthy",
            result_summary=result_summary,
            should_notify=should_notify,
            context_payload=context_payload,
        )
