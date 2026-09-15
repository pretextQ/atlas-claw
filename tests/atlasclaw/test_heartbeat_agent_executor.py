# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from app.atlasclaw.agent.prompt_sections import build_heartbeats
from app.atlasclaw.heartbeat.agent_executor import AgentHeartbeatExecutor
from app.atlasclaw.heartbeat.events import build_heartbeat_event_payload
from app.atlasclaw.heartbeat.models import HeartbeatJobDefinition, HeartbeatJobType


def test_build_heartbeats_renders_heartbeat_md_content() -> None:
    rendered = build_heartbeats(
        heartbeat_markdown="# Heartbeat\nCheck pending approvals.",
        every_seconds=3600,
        active_hours="09:00-22:00",
        isolated_session=True,
    )

    assert "Check pending approvals." in rendered
    assert "3600" in rendered
    assert "isolated" in rendered.lower()


def test_build_heartbeat_event_payload_contains_standard_fields() -> None:
    payload = build_heartbeat_event_payload(
        job_id="hb-1",
        job_type="agent_turn",
        result_summary="HEARTBEAT_OK",
        status="healthy",
    )

    assert payload["job_id"] == "hb-1"
    assert payload["job_type"] == "agent_turn"
    assert payload["result_summary"] == "HEARTBEAT_OK"
    assert payload["status"] == "healthy"


async def _fake_agent_runner(job: HeartbeatJobDefinition) -> dict[str, object]:
    return {
        "assistant_message": "HEARTBEAT_OK",
        "system_prompt": "heartbeat system prompt",
        "message_history": [{"role": "user", "content": "check status"}],
        "tool_calls": [],
        "session_title": "Heartbeat Session",
    }


@pytest.mark.asyncio
async def test_agent_heartbeat_treats_heartbeat_ok_as_silent_success() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-agent-main",
        job_type=HeartbeatJobType.AGENT_TURN,
        owner_user_id="admin",
    )
    executor = AgentHeartbeatExecutor(agent_runner=_fake_agent_runner)

    result = await executor.execute(job)

    assert result.status == "healthy"
    assert result.result_summary == "HEARTBEAT_OK"
    assert result.should_notify is False


@pytest.mark.asyncio
async def test_agent_heartbeat_emits_context_ready_payload() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-agent-main",
        job_type=HeartbeatJobType.AGENT_TURN,
        owner_user_id="admin",
    )
    executor = AgentHeartbeatExecutor(agent_runner=_fake_agent_runner)

    result = await executor.execute(job)

    assert result.context_payload["job_type"] == "agent_turn"
    assert result.context_payload["assistant_message"] == "HEARTBEAT_OK"
