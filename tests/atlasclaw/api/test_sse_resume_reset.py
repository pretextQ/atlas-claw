# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-10 regression tests: SSE resume semantics, buffer config, serialization.

Covers: an evicted/forged Last-Event-ID must produce an explicit reset
signal instead of a silent empty replay (F-0027), max_events_buffer must
actually size the replay buffer, non-JSON-serializable payloads must not
crash the stream, and retry=0 must be honored.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.atlasclaw.api.sse import (
    SSEEvent,
    SSEEventType,
    SSEManager,
    StreamState,
)


async def _drain(generator, count: int, timeout: float = 0.2):
    events = []
    for _ in range(count):
        events.append(await asyncio.wait_for(generator.__anext__(), timeout=timeout))
    return events


class TestResumeResetSemantics:
    @pytest.mark.asyncio
    async def test_evicted_last_event_id_signals_reset(self):
        """A Last-Event-ID pushed out of the buffer must not silently replay
        nothing — the client gets an explicit stream_reset error (F-0027)."""
        manager = SSEManager(heartbeat_interval=0.01, stream_timeout=5.0, max_events_buffer=5)
        run_id = "run-reset"
        manager.create_stream(run_id)
        for i in range(12):
            manager.push_assistant(run_id, f"msg-{i}")

        # Event 1 has been evicted by the bounded buffer.
        generator = manager._event_generator(run_id, last_event_id=f"{run_id}-1")
        first = await asyncio.wait_for(generator.__anext__(), timeout=0.2)

        assert first["event"] == "error"
        payload = json.loads(first["data"])
        assert payload["code"] == "stream_reset"
        assert payload["last_event_id"] == f"{run_id}-1"

        # And the full buffer follows so no events are silently lost.
        replayed = await _drain(generator, 5)
        assert all(e["event"] == "assistant" for e in replayed)
        await generator.aclose()

    @pytest.mark.asyncio
    async def test_forged_last_event_id_signals_reset(self):
        manager = SSEManager(heartbeat_interval=0.01, stream_timeout=5.0)
        run_id = "run-forged"
        manager.create_stream(run_id)
        manager.push_assistant(run_id, "hello")

        generator = manager._event_generator(run_id, last_event_id="not-a-real-id")
        first = await asyncio.wait_for(generator.__anext__(), timeout=0.2)
        assert first["event"] == "error"
        assert json.loads(first["data"])["code"] == "stream_reset"
        await generator.aclose()

    @pytest.mark.asyncio
    async def test_known_last_event_id_replays_only_newer_events(self):
        manager = SSEManager(heartbeat_interval=0.01, stream_timeout=5.0)
        run_id = "run-known"
        manager.create_stream(run_id)
        manager.push_lifecycle(run_id, "start")
        manager.push_assistant(run_id, "one")
        manager.push_assistant(run_id, "two")

        generator = manager._event_generator(run_id, last_event_id=f"{run_id}-1")
        replayed = await _drain(generator, 2)
        assert '"text": "one"' in replayed[0]["data"]
        assert '"text": "two"' in replayed[1]["data"]
        await generator.aclose()


class TestBufferConfiguration:
    def test_max_events_buffer_sizes_the_replay_buffer(self):
        """The configured buffer size must bound the replay ring."""
        manager = SSEManager(max_events_buffer=5)
        stream = manager.create_stream("run-buf")
        for i in range(30):
            manager.push_assistant("run-buf", f"m{i}")

        assert len(stream.events) == 5
        assert stream.events[-1].event_id == "run-buf-30"

    def test_default_buffer_is_100(self):
        manager = SSEManager()
        stream = manager.create_stream("run-default")
        for i in range(120):
            manager.push_assistant("run-default", f"m{i}")
        assert len(stream.events) == 100


class TestSerialization:
    def test_non_json_types_do_not_crash_serialization(self):
        """datetime/set members used to make json.dumps recurse infinitely."""
        event = SSEEvent(
            event_type=SSEEventType.ASSISTANT,
            data={
                "when": datetime(2026, 9, 29, tzinfo=timezone.utc),
                "tags": {"alpha", "beta"},
                "text": "ok",
            },
        )
        formatted = event.to_sse_format()
        payload = json.loads(formatted["data"])
        assert payload["text"] == "ok"
        assert "2026-09-29" in payload["when"]

    def test_retry_zero_is_preserved(self):
        """retry=0 means 'reconnect immediately' and must not be dropped."""
        event = SSEEvent(event_type=SSEEventType.LIFECYCLE, data={"phase": "end"}, retry=0)
        assert event.to_sse_format()["retry"] == 0

    def test_retry_none_is_omitted(self):
        event = SSEEvent(event_type=SSEEventType.LIFECYCLE, data={"phase": "end"})
        assert "retry" not in event.to_sse_format()


class TestDeadCodeRemoved:
    def test_remove_self_from_no_longer_exists(self):
        from app.atlasclaw.api import sse as sse_module

        assert not hasattr(sse_module._SubscriberQueue, "remove_self_from")
