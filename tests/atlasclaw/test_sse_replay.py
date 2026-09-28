# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

import asyncio
import json

import pytest

from app.atlasclaw.api.sse import SSEEvent, SSEEventType, SSEManager


@pytest.mark.asyncio
async def test_late_subscriber_replays_buffered_events_without_last_event_id():
    manager = SSEManager(heartbeat_interval=0.01, stream_timeout=1.0)
    run_id = "run-replay"

    manager.create_stream(run_id)
    manager.push_lifecycle(run_id, "start")
    manager.push_assistant(run_id, "hello")

    generator = manager._event_generator(run_id)

    first_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)
    second_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    manager.close_stream(run_id)
    third_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    assert first_event["event"] == "lifecycle"
    assert '"phase": "start"' in first_event["data"]
    assert second_event["event"] == "assistant"
    assert '"text": "hello"' in second_event["data"]
    assert third_event["event"] == "lifecycle"
    assert '"phase": "end"' in third_event["data"]


@pytest.mark.asyncio
async def test_closed_stream_replay_emits_lifecycle_end_for_late_subscriber():
    manager = SSEManager(heartbeat_interval=0.01, stream_timeout=1.0)
    run_id = "run-closed-replay"

    manager.create_stream(run_id)
    manager.push_lifecycle(run_id, "start")
    manager.push_assistant(run_id, "hello")
    manager.close_stream(run_id)

    generator = manager._event_generator(run_id)

    first_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)
    second_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)
    third_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    assert first_event["event"] == "lifecycle"
    assert '"phase": "start"' in first_event["data"]
    assert second_event["event"] == "assistant"
    assert '"text": "hello"' in second_event["data"]
    assert third_event["event"] == "lifecycle"
    assert '"phase": "end"' in third_event["data"]


@pytest.mark.asyncio
async def test_aborted_stream_emits_one_terminal_lifecycle_when_closed_twice():
    manager = SSEManager(heartbeat_interval=0.01, stream_timeout=1.0)
    run_id = "run-aborted"

    manager.create_stream(run_id)
    manager.push_lifecycle(run_id, "start")
    generator = manager._event_generator(run_id)
    first_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    manager.push_lifecycle(run_id, "aborted")
    manager.close_stream(run_id)
    manager.close_stream(run_id)
    second_event = await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(generator.__anext__(), timeout=0.1)

    phases = [
        json.loads(event["data"])["phase"]
        for event in (first_event, second_event)
    ]
    assert phases == ["start", "aborted"]


@pytest.mark.asyncio
async def test_aborted_stream_reconnect_after_terminal_event_emits_no_end():
    manager = SSEManager(heartbeat_interval=0.01, stream_timeout=1.0)
    run_id = "run-aborted-reconnect"

    stream = manager.create_stream(run_id)
    manager.push_lifecycle(run_id, "start")
    manager.push_lifecycle(run_id, "aborted")
    manager.close_stream(run_id)
    aborted_event_id = stream.events[-1].event_id

    generator = manager._event_generator(
        run_id,
        last_event_id=aborted_event_id,
    )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(generator.__anext__(), timeout=0.1)


def test_push_assistant_strips_tool_meta_block_contents():
    manager = SSEManager()
    run_id = "run-tool-meta"

    manager.create_stream(run_id)
    manager.push_assistant(
        run_id,
        "Visible before <tool_meta>secret internal metadata</tool_meta> visible after",
    )

    stream = manager.get_stream(run_id)

    assert stream is not None
    assert stream.events[-1].data["text"] == "Visible before  visible after"


def test_subscriber_queue_overflow_marks_broken_and_signals():
    """A full subscriber queue must not silently drop events."""
    from app.atlasclaw.api.sse import (
        SSEEvent,
        SSEEventType,
        _StreamOverflow,
        _SubscriberQueue,
    )

    queue = _SubscriberQueue(maxsize=2)
    first = SSEEvent(SSEEventType.ASSISTANT, {"text": "1"})
    second = SSEEvent(SSEEventType.ASSISTANT, {"text": "2"})
    third = SSEEvent(SSEEventType.ASSISTANT, {"text": "3"})

    assert queue.put_event(first) is True
    assert queue.put_event(second) is True
    # Third push overflows: the queue is marked broken and a sentinel is
    # queued after displacing the oldest event.
    assert queue.put_event(third) is False

    items = []
    while not queue._queue.empty():
        items.append(queue._queue.get_nowait())
    assert items[0] is second
    assert isinstance(items[1], _StreamOverflow)

    # A broken subscriber no longer accepts events; the generator closes the
    # stream so the client can reconnect with Last-Event-ID.
    assert queue.put_event(third) is False


def test_push_event_returns_only_accepted_subscribers():
    from app.atlasclaw.api.sse import (
        SSEEvent,
        SSEEventType,
        _SubscriberQueue,
    )

    manager = SSEManager()
    run_id = "run-notified"
    manager.create_stream(run_id)
    healthy = _SubscriberQueue(maxsize=10)
    broken = _SubscriberQueue(maxsize=1)
    broken.broken = True
    manager._subscribers[run_id] = [healthy, broken]

    notified = manager.push_event(run_id, SSEEvent(SSEEventType.ASSISTANT, {"text": "x"}))

    assert notified == 1


def test_close_stream_delivers_terminator_on_full_queue():
    """Stream close must reach subscribers even when their queue is full."""
    from app.atlasclaw.api.sse import (
        SSEEvent,
        SSEEventType,
        _SubscriberQueue,
    )

    manager = SSEManager()
    run_id = "run-close-full"
    manager.create_stream(run_id)
    queue = _SubscriberQueue(maxsize=2)
    manager._subscribers[run_id] = [queue]
    for i in range(2):
        queue.put_event(SSEEvent(SSEEventType.ASSISTANT, {"text": str(i)}))

    manager.close_stream(run_id)

    items = []
    while not queue._queue.empty():
        items.append(queue._queue.get_nowait())
    assert items[-1] is None


@pytest.mark.asyncio
async def test_live_overflow_yields_explicit_error_and_closes_stream():
    """A slow client gets an explicit overflow error, then a closed stream."""
    manager = SSEManager(heartbeat_interval=0.01, stream_timeout=1.0)
    run_id = "run-overflow"
    manager.create_stream(run_id)

    generator = manager._event_generator(run_id)
    # The generator registers its subscriber queue and emits a heartbeat
    # while idle.
    first_event = await asyncio.wait_for(generator.__anext__(), timeout=0.5)
    assert first_event["event"] == "heartbeat"

    # Flood the subscriber without consuming: pushes overflow the queue.
    for i in range(manager._max_events_buffer + 5):
        manager.push_event(run_id, SSEEvent(SSEEventType.ASSISTANT, {"text": f"t{i}"}))

    events = []
    while True:
        event = await asyncio.wait_for(generator.__anext__(), timeout=0.5)
        events.append(event)
        if event["event"] == "error":
            break
    assert '"stream_overflow"' in events[-1]["data"]

    # The generator closed the stream after the overflow error so the client
    # reconnects with Last-Event-ID and replays from the buffer.
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(generator.__anext__(), timeout=0.5)
