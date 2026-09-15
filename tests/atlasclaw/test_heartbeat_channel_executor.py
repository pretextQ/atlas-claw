# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from app.atlasclaw.heartbeat.channel_executor import ChannelHeartbeatExecutor
from app.atlasclaw.heartbeat.models import HeartbeatJobDefinition, HeartbeatJobType


async def _failing_channel_probe(job: HeartbeatJobDefinition) -> dict[str, object]:
    return {
        "healthy": False,
        "status": "disconnected",
        "reconnected": False,
        "summary": "connection_failed",
    }


async def _reconnecting_channel_probe(job: HeartbeatJobDefinition) -> dict[str, object]:
    return {
        "healthy": False,
        "status": "disconnected",
        "reconnected": False,
        "reconnect_attempted": True,
        "summary": "reconnect_failed",
    }


async def _reconnected_channel_probe(job: HeartbeatJobDefinition) -> dict[str, object]:
    return {
        "healthy": True,
        "status": "connected",
        "reconnected": True,
        "summary": "reconnected",
    }


@pytest.mark.asyncio
async def test_channel_heartbeat_retries_quietly_before_degraded() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-channel-1",
        job_type=HeartbeatJobType.CHANNEL_CONNECTION,
        owner_user_id="admin",
    )
    executor = ChannelHeartbeatExecutor(channel_probe=_failing_channel_probe, failure_threshold=3)

    first = await executor.execute(job)
    second = await executor.execute(job)

    assert first.status == "failed"
    assert first.should_alert is False
    assert second.should_alert is False


@pytest.mark.asyncio
async def test_channel_heartbeat_marks_degraded_after_threshold() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-channel-1",
        job_type=HeartbeatJobType.CHANNEL_CONNECTION,
        owner_user_id="admin",
    )
    executor = ChannelHeartbeatExecutor(channel_probe=_failing_channel_probe, failure_threshold=3)

    await executor.execute(job)
    await executor.execute(job)
    result = await executor.execute(job)

    assert result.status == "degraded"
    assert result.should_alert is True


@pytest.mark.asyncio
async def test_channel_heartbeat_uses_backoff_delay_after_failures() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-channel-backoff",
        job_type=HeartbeatJobType.CHANNEL_CONNECTION,
        owner_user_id="admin",
    )
    executor = ChannelHeartbeatExecutor(
        channel_probe=_failing_channel_probe,
        failure_threshold=3,
        reconnect_backoff_seconds=[5, 30, 60],
    )

    first = await executor.execute(job)
    second = await executor.execute(job)
    third = await executor.execute(job)

    assert first.next_delay_seconds == 5
    assert second.next_delay_seconds == 30
    assert third.next_delay_seconds == 60


@pytest.mark.asyncio
async def test_channel_heartbeat_emits_reconnect_transition_events_on_failure() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-channel-1",
        job_type=HeartbeatJobType.CHANNEL_CONNECTION,
        owner_user_id="admin",
    )
    executor = ChannelHeartbeatExecutor(channel_probe=_reconnecting_channel_probe, failure_threshold=3)

    result = await executor.execute(job)

    assert "heartbeat.channel.reconnect_started" in result.extra_event_types
    assert "heartbeat.channel.reconnect_failed" in result.extra_event_types


@pytest.mark.asyncio
async def test_channel_heartbeat_emits_reconnect_succeeded_event() -> None:
    job = HeartbeatJobDefinition(
        job_id="hb-channel-1",
        job_type=HeartbeatJobType.CHANNEL_CONNECTION,
        owner_user_id="admin",
    )
    executor = ChannelHeartbeatExecutor(channel_probe=_reconnected_channel_probe, failure_threshold=3)

    result = await executor.execute(job)

    assert result.status == "healthy"
    assert "heartbeat.channel.reconnect_succeeded" in result.extra_event_types
