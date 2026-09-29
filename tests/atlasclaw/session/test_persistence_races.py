# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-07 regression tests: session persistence races and lock scope.

Covers: the full-cache metadata save must be serialized across sessions
(F-0047), transcript appends/rewrites must run under the per-session lock
(F-0048), the queue must reject cap < 1 at construction instead of
IndexError at runtime, and idle per-session queue state must be reclaimed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.atlasclaw.core.config_schema import ResetMode
from app.atlasclaw.session.context import TranscriptEntry
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue


def _make_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(
        workspace_path=str(tmp_path),
        user_id="u1",
        reset_mode=ResetMode.MANUAL,
    )


class TestQueueConstruction:
    def test_cap_below_one_rejected_at_construction(self):
        """cap=0 used to construct fine and blow up with IndexError later."""
        with pytest.raises(ValueError):
            SessionQueue(cap=0)

    def test_max_concurrent_below_one_rejected(self):
        with pytest.raises(ValueError):
            SessionQueue(max_concurrent=0)


class TestQueueStateReclaim:
    @pytest.mark.asyncio
    async def test_idle_session_state_is_reclaimed(self):
        queue = SessionQueue()
        queue._max_tracked_sessions = 4
        for i in range(6):
            key = f"agent:main:user:u{i}:web:dm:p{i}"
            assert await queue.acquire(key) is True
            queue.release(key)
            assert queue._session_refs[key] == 0
        assert len(queue._locks) <= 4
        assert len(queue._queued) <= 4

    @pytest.mark.asyncio
    async def test_active_session_state_is_kept(self):
        queue = SessionQueue()
        queue._max_tracked_sessions = 1
        assert await queue.acquire("agent:main:user:u1:web:dm:p1") is True
        queue._evict_idle_sessions()
        assert "agent:main:user:u1:web:dm:p1" in queue._locks
        queue.release("agent:main:user:u1:web:dm:p1")


class TestMetadataSaveSerialization:
    @pytest.mark.asyncio
    async def test_concurrent_saves_do_not_lose_sessions(self, tmp_path: Path):
        """A save started before another session is created must not clobber
        the newer snapshot on disk (F-0047 lost update)."""
        manager = _make_manager(tmp_path)
        key_a = "agent:main:user:u1:web:dm:a"
        key_b = "agent:main:user:u1:web:dm:b"

        await manager.get_or_create(key_a)

        original_replace = manager._replace_file_with_retry

        async def yielding_replace(tmp_path_arg, dst):
            # Deterministic interleave point: the older save has already
            # snapshotted {A} and written its tmp file; yield so the newer
            # save (with {A, B}) completes its replace first.
            await asyncio.sleep(0)
            await original_replace(tmp_path_arg, dst)

        manager._replace_file_with_retry = yielding_replace

        save_task = asyncio.create_task(manager._save_metadata())
        await asyncio.sleep(0)
        await manager.get_or_create(key_b)
        await save_task

        metadata_path = manager.sessions_dir / manager.METADATA_FILE
        final = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert key_a in final
        assert key_b in final


class TestTranscriptLockScope:
    @pytest.mark.asyncio
    async def test_append_transcript_runs_under_session_lock(self, tmp_path: Path):
        """The append must hold the per-session lock while it writes (F-0048)."""
        manager = _make_manager(tmp_path)
        session_key = "agent:main:user:u1:web:dm:p1"
        observed = {}
        original_save = manager._save_metadata

        async def spy_save():
            lock = manager._locks.get(session_key)
            observed["locked"] = bool(lock is not None and lock.locked())
            return await original_save()

        manager._save_metadata = spy_save

        await manager.append_transcript(
            session_key, TranscriptEntry(role="user", content="hi")
        )
        assert observed["locked"] is True

    @pytest.mark.asyncio
    async def test_persist_transcript_runs_under_session_lock(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        session_key = "agent:main:user:u1:web:dm:p1"
        observed = {}
        original_save = manager._save_metadata

        async def spy_save():
            lock = manager._locks.get(session_key)
            observed["locked"] = bool(lock is not None and lock.locked())
            return await original_save()

        manager._save_metadata = spy_save

        await manager.persist_transcript(session_key, [{"role": "user", "content": "hi"}])
        assert observed["locked"] is True
