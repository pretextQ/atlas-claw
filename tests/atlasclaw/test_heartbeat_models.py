# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.core.config_schema import AtlasClawConfig
from app.atlasclaw.heartbeat.models import (
    HeartbeatJobStateSnapshot,
    HeartbeatJobType,
    HeartbeatTargetType,
)


def test_atlasclaw_config_parses_heartbeat_sections() -> None:
    config = AtlasClawConfig.model_validate(
        {
            "heartbeat": {
                "enabled": True,
                "runtime": {
                    "tick_seconds": 15,
                    "max_concurrent_jobs": 8,
                    "emit_runtime_events": False,
                    "persist_local_event_log": False,
                },
                "agent_turn": {
                    "enabled": True,
                    "every_seconds": 1800,
                    "heartbeat_file": "HEARTBEAT.md",
                },
                "channel_connection": {
                    "enabled": True,
                    "failure_threshold": 5,
                    "degraded_threshold": 4,
                },
            }
        }
    )

    assert config.heartbeat.enabled is True
    assert config.heartbeat.runtime.tick_seconds == 15
    assert config.heartbeat.runtime.emit_runtime_events is False
    assert config.heartbeat.runtime.persist_local_event_log is False
    assert config.heartbeat.agent_turn.every_seconds == 1800
    assert config.heartbeat.agent_turn.heartbeat_file == "HEARTBEAT.md"
    assert config.heartbeat.channel_connection.failure_threshold == 5
    assert config.heartbeat.channel_connection.degraded_threshold == 4


def test_heartbeat_state_snapshot_round_trips() -> None:
    snapshot = HeartbeatJobStateSnapshot(
        job_id="hb-1",
        job_type=HeartbeatJobType.AGENT_TURN,
        status="healthy",
        consecutive_failures=2,
        last_error="temporary timeout",
    )

    restored = HeartbeatJobStateSnapshot.from_dict(snapshot.to_dict())

    assert restored.job_id == "hb-1"
    assert restored.job_type == HeartbeatJobType.AGENT_TURN
    assert restored.status == "healthy"
    assert restored.consecutive_failures == 2
    assert restored.last_error == "temporary timeout"


def test_target_descriptor_parses_thread_target() -> None:
    config = AtlasClawConfig.model_validate(
        {
            "heartbeat": {
                "enabled": True,
                "agent_turn": {
                    "enabled": True,
                    "target": {
                        "type": "thread",
                        "user_id": "admin",
                        "channel": "web",
                        "thread_id": "thread-1",
                    },
                },
            }
        }
    )

    assert config.heartbeat.agent_turn.target.type == HeartbeatTargetType.THREAD
    assert config.heartbeat.agent_turn.target.thread_id == "thread-1"
