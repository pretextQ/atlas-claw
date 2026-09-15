# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from pathlib import Path

from app.atlasclaw.heartbeat.models import (
    HeartbeatJobDefinition,
    HeartbeatJobStateSnapshot,
    HeartbeatJobType,
)
from app.atlasclaw.heartbeat.store import HeartbeatStateStore


def test_store_persists_jobs_and_state(tmp_path: Path) -> None:
    store = HeartbeatStateStore(workspace_path=str(tmp_path))
    job = HeartbeatJobDefinition(
        job_id="hb-agent-main",
        job_type=HeartbeatJobType.AGENT_TURN,
        owner_user_id="admin",
    )
    snapshot = HeartbeatJobStateSnapshot(
        job_id="hb-agent-main",
        job_type=HeartbeatJobType.AGENT_TURN,
        status="scheduled",
    )

    store.save_jobs("admin", [job])
    store.save_state("admin", [snapshot])

    assert store.load_jobs("admin")[0].job_id == "hb-agent-main"
    assert store.load_state("admin")[0].status == "scheduled"
