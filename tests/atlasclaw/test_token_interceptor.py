# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the token health interceptor's non-blocking persistence."""

from __future__ import annotations

import asyncio
import threading

from app.atlasclaw.core.token_interceptor import TokenHealthInterceptor
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool


class _RecordingHealthStore:
    """Health store double that records the thread each save runs on."""

    def __init__(self):
        self.saved: list[dict] = []
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def save(self, health_status: dict) -> None:
        with self._lock:
            self.saved.append(dict(health_status))
            self.threads.append(threading.current_thread())


def _build_pool() -> TokenPool:
    pool = TokenPool()
    pool.register_token(
        TokenEntry(
            token_id="token-a",
            provider="openai",
            model="gpt-test",
            base_url="https://api.example.com",
            api_key="sk-test",
        )
    )
    return pool


def test_on_response_persists_without_blocking_event_loop():
    """Saves run in a worker thread, not on the event loop thread."""
    pool = _build_pool()
    store = _RecordingHealthStore()
    interceptor = TokenHealthInterceptor(pool, store)

    async def scenario():
        loop_thread = threading.current_thread()
        interceptor.on_response("token-a", {"X-RateLimit-Remaining": "42"})
        assert interceptor._save_task is not None
        await interceptor._save_task

        assert len(store.saved) == 1
        assert "token-a" in store.saved[0]
        assert store.threads[0] is not loop_thread

    asyncio.run(scenario())


def test_hard_failure_schedules_chained_save():
    """Concurrent saves are chained so snapshots persist in call order."""
    pool = _build_pool()
    store = _RecordingHealthStore()
    interceptor = TokenHealthInterceptor(pool, store)

    async def scenario():
        interceptor.on_response("token-a", {"X-RateLimit-Remaining": "1"})
        interceptor.on_hard_failure("token-a", "boom")
        task = interceptor._save_task
        assert task is not None
        await task

        assert len(store.saved) == 2

    asyncio.run(scenario())


def test_on_response_ignores_non_rate_limit_headers():
    pool = _build_pool()
    store = _RecordingHealthStore()
    interceptor = TokenHealthInterceptor(pool, store)

    async def scenario():
        interceptor.on_response("token-a", {"Content-Type": "application/json"})
        await asyncio.sleep(0)
        assert store.saved == []
        assert interceptor._save_task is None

    asyncio.run(scenario())
