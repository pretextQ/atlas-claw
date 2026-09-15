# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.atlasclaw.hooks.runtime_models import (
    HookEventEnvelope,
    HookEventType,
    PendingHookItem,
)
from app.atlasclaw.hooks.runtime_store import HookStateStore


@pytest.mark.asyncio
async def test_hook_state_store_isolates_events_per_user(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    event = HookEventEnvelope(
        id="evt-1",
        event_type=HookEventType.RUN_STARTED,
        user_id="user-a",
        session_key="agent:main:user:user-a:main",
        run_id="run-1",
        channel="web",
        agent_id="main",
        created_at=datetime.now(timezone.utc),
        payload={"message": "hello"},
    )

    await store.append_event("audit", event)

    user_a_events = await store.list_events("audit", "user-a")
    user_b_events = await store.list_events("audit", "user-b")

    assert [item.id for item in user_a_events] == ["evt-1"]
    assert user_b_events == []


@pytest.mark.asyncio
async def test_hook_state_store_tracks_pending_item_lifecycle(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    pending = PendingHookItem(
        id="pending-1",
        module_name="audit",
        user_id="user-a",
        source_event_ids=["evt-1"],
        summary="Review the captured runtime anomaly",
        payload={"kind": "anomaly"},
        status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    await store.append_pending("audit", pending)
    initial_pending = await store.list_pending("audit", "user-a")
    assert [item.id for item in initial_pending] == ["pending-1"]

    confirmed = await store.resolve_pending(
        module_name="audit",
        user_id="user-a",
        pending_id="pending-1",
        decision="confirm",
        decided_by="user-a",
        note="looks good",
    )

    assert confirmed.status == "confirmed"
    assert await store.list_pending("audit", "user-a") == []

    decisions = await store.list_decisions("audit", "user-a")
    assert len(decisions) == 1
    assert decisions[0].pending_id == "pending-1"
    assert decisions[0].decision == "confirm"


@pytest.mark.asyncio
async def test_hook_state_store_reject_records_decision_and_hides_pending(tmp_path):
    store = HookStateStore(workspace_path=str(tmp_path))
    pending = PendingHookItem(
        id="pending-2",
        module_name="audit",
        user_id="user-a",
        source_event_ids=["evt-2"],
        summary="Reject this candidate",
        payload={"kind": "candidate"},
        status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    await store.append_pending("audit", pending)
    rejected = await store.resolve_pending(
        module_name="audit",
        user_id="user-a",
        pending_id="pending-2",
        decision="reject",
        decided_by="user-a",
        note="not useful",
    )

    assert rejected.status == "rejected"
    assert await store.list_pending("audit", "user-a") == []

    decisions = await store.list_decisions("audit", "user-a")
    assert len(decisions) == 1
    assert decisions[0].decision == "reject"
