# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.atlasclaw.heartbeat.events import emit_heartbeat_event_to_hook_runtime
from app.atlasclaw.heartbeat.models import HeartbeatEventEnvelope, HeartbeatEventType, HeartbeatJobType
from app.atlasclaw.hooks.runtime import HookHandlerDefinition, HookRuntime, HookRuntimeContext
from app.atlasclaw.hooks.runtime_models import HookEventType
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore


@dataclass
class _Recorder:
    seen: list[HookEventType] = field(default_factory=list)

    async def __call__(self, event, runtime_context) -> None:
        self.seen.append(event.event_type)


@pytest.mark.asyncio
async def test_emit_heartbeat_event_to_hook_runtime_bridges_event_type(tmp_path) -> None:
    store = HookStateStore(workspace_path=str(tmp_path))
    runtime = HookRuntime(
        HookRuntimeContext(
            workspace_path=str(tmp_path),
            hook_state_store=store,
            memory_sink=MemorySink(str(tmp_path)),
            context_sink=ContextSink(store),
        )
    )
    recorder = _Recorder()
    runtime.register(
        HookHandlerDefinition(
            module_name="heartbeat-audit",
            event_types={HookEventType.HEARTBEAT_AGENT_COMPLETED},
            handler=recorder,
        )
    )
    event = HeartbeatEventEnvelope(
        id="hb-evt-1",
        event_type=HeartbeatEventType.AGENT_COMPLETED,
        job_id="hb-agent-main",
        job_type=HeartbeatJobType.AGENT_TURN,
        user_id="admin",
        created_at=datetime.now(timezone.utc),
        payload={"result_summary": "HEARTBEAT_OK"},
    )

    await emit_heartbeat_event_to_hook_runtime(runtime, event)

    assert recorder.seen == [HookEventType.HEARTBEAT_AGENT_COMPLETED]
