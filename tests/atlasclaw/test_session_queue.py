# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the session serialization queue."""

from __future__ import annotations

import asyncio

import pytest

from app.atlasclaw.session.queue import SessionQueue


@pytest.mark.asyncio
async def test_acquire_serializes_runs_per_session():
    """Two runs in one session must not overlap."""
    queue = SessionQueue(max_concurrent=4)
    order: list[str] = []

    async def run(label: str) -> None:
        await queue.acquire("session-1")
        try:
            order.append(f"{label}-start")
            await asyncio.sleep(0.05)
            order.append(f"{label}-end")
        finally:
            queue.release("session-1")

    await asyncio.gather(run("a"), run("b"))

    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )


@pytest.mark.asyncio
async def test_acquire_allows_independent_sessions_to_overlap():
    """Different sessions may run concurrently."""
    queue = SessionQueue(max_concurrent=4)
    started = asyncio.Event()
    both_running = asyncio.Event()
    running = 0

    async def run(session_key: str) -> None:
        nonlocal running
        await queue.acquire(session_key)
        try:
            running += 1
            if running == 2:
                both_running.set()
            started.set()
            await asyncio.wait_for(both_running.wait(), timeout=1.0)
        finally:
            running -= 1
            queue.release(session_key)

    await asyncio.gather(run("session-a"), run("session-b"))

    assert both_running.is_set()


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_leak_global_permit():
    """A cancelled acquire must not hold a global permit it never used.

    With global-first acquisition the cancelled call would still be holding a
    global permit while parked on the session lock, permanently shrinking the
    concurrency budget.
    """
    queue = SessionQueue(max_concurrent=2)
    await queue.acquire("session-1")  # the active run holds one permit
    assert queue._global_semaphore._value == 1

    waiter = asyncio.create_task(queue.acquire("session-1"))
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # Only the active run holds a permit; the cancelled acquire left nothing.
    assert queue._global_semaphore._value == 1

    queue.release("session-1")
    assert queue._global_semaphore._value == 2

    # The session slot is free again too.
    assert await asyncio.wait_for(queue.acquire("session-1"), timeout=1.0) is True
    queue.release("session-1")


@pytest.mark.asyncio
async def test_release_without_active_run_does_not_inflate_permits():
    """A duplicate release must not add permits beyond the configured limit."""
    queue = SessionQueue(max_concurrent=1)

    await queue.acquire("session-1")
    queue.release("session-1")
    queue.release("session-1")  # spurious extra release

    # With one permit, a second session can still acquire exactly once; if the
    # extra release had inflated the semaphore this would exceed max_concurrent.
    await asyncio.wait_for(queue.acquire("session-2"), timeout=1.0)
    assert queue._global_semaphore._value == 0
    queue.release("session-2")


@pytest.mark.asyncio
async def test_execute_agent_run_uses_session_queue(monkeypatch):
    """The HTTP run path must hold the session slot for the whole run."""
    from types import SimpleNamespace

    from app.atlasclaw.api.services import run_service

    queue = SessionQueue(max_concurrent=1)
    observed: dict = {}

    async def fake_runner_run(**kwargs):
        del kwargs
        observed["active_during_run"] = queue.is_active("session-1")
        if False:
            yield None

    ctx = SimpleNamespace(
        session_queue=queue,
        agent_runners={},
        agent_runner=SimpleNamespace(run=fake_runner_run),
        active_runs={
            "run-1": {
                "status": "running",
                "abort_signal": asyncio.Event(),
                "session_key": "session-1",
            }
        },
        sse_manager=SimpleNamespace(
            close_stream=lambda run_id: None,
            push_lifecycle=lambda *a, **k: 0,
            push_error=lambda *a, **k: 0,
        ),
    )

    monkeypatch.setattr(run_service, "build_scoped_deps", lambda *a, **k: SimpleNamespace(
        abort_signal=asyncio.Event(),
        is_aborted=lambda: False,
    ))
    monkeypatch.setattr(
        run_service,
        "_iterate_until_aborted",
        lambda events, signal: events,
    )
    monkeypatch.setattr(
        run_service,
        "_transition_running_run",
        lambda run_info, status_value, error=None: True,
    )
    monkeypatch.setattr(run_service, "abort_run", lambda ctx_arg, run_id: None)

    await run_service.execute_agent_run(
        ctx,
        run_id="run-1",
        session_key="session-1",
        message="hello",
        timeout_seconds=30,
    )

    assert observed["active_during_run"] is True
    # Released afterwards: the permit is available again.
    assert queue._global_semaphore._value == 1
