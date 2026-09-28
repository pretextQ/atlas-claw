# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Resilience tests for session transcript and metadata persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.atlasclaw.session.context import TranscriptEntry
from app.atlasclaw.session.manager import SessionManager


pytestmark = pytest.mark.asyncio

SESSION_KEY = "agent:main:user:u1:web:dm:p1"


def _make_manager(tmp_path: Path, user_id: str = "u1") -> SessionManager:
    return SessionManager(workspace_path=tmp_path / "workspace", user_id=user_id)


async def _append(manager: SessionManager, role: str, content: str) -> None:
    await manager.append_transcript(
        SESSION_KEY,
        TranscriptEntry(role=role, content=content),
    )


class TestTranscriptLineResilience:
    async def test_corrupt_line_is_skipped_instead_of_wiping_history(
        self, tmp_path: Path
    ):
        manager = _make_manager(tmp_path)
        await _append(manager, "user", "first message")
        await _append(manager, "assistant", "reply one")

        session = await manager.get_or_create(SESSION_KEY)
        transcript_path = manager._get_transcript_path(session)
        with transcript_path.open("a", encoding="utf-8") as handle:
            handle.write('{"role": "user", "content": "torn li')
            handle.write("\n")
        await _append(manager, "user", "message after corruption")

        entries = await manager.load_transcript(SESSION_KEY)

        contents = [entry.content for entry in entries]
        assert "first message" in contents
        assert "reply one" in contents
        assert "message after corruption" in contents
        assert len(entries) == 3

    async def test_rewrite_keeps_recovered_entries(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        await _append(manager, "user", "keep me")

        session = await manager.get_or_create(SESSION_KEY)
        transcript_path = manager._get_transcript_path(session)
        with transcript_path.open("a", encoding="utf-8") as handle:
            handle.write("not json at all\n")

        entries = await manager.load_transcript(SESSION_KEY)
        await manager.persist_transcript(
            SESSION_KEY,
            [{"role": entry.role, "content": entry.content} for entry in entries],
        )

        reloaded = await manager.load_transcript(SESSION_KEY)
        assert [entry.content for entry in reloaded] == ["keep me"]


class TestMetadataLoadFailure:
    async def test_failed_metadata_load_does_not_overwrite_index(
        self, tmp_path: Path
    ):
        workspace = tmp_path / "workspace"
        first = SessionManager(workspace_path=workspace, user_id="u1")
        await _append(first, "user", "one")
        first_key_two = "agent:main:user:u1:web:dm:p2"
        await first.append_transcript(
            first_key_two,
            TranscriptEntry(role="user", content="two"),
        )
        first_sessions_json = first.sessions_dir / first.METADATA_FILE
        healthy_content = first_sessions_json.read_text(encoding="utf-8")
        assert len(first._metadata_cache) == 2

        # A second manager instance reads the same index, but the file was
        # damaged (for example by an interrupted manual edit).
        damaged_content = healthy_content[: max(1, len(healthy_content) // 3)]
        first_sessions_json.write_text(damaged_content, encoding="utf-8")

        second = SessionManager(workspace_path=workspace, user_id="u1")
        await second.get_or_create(SESSION_KEY)

        # The save must be refused: writing only the in-process cache would
        # orphan every session not loaded here.
        assert second._metadata_load_failed is True
        assert (
            first_sessions_json.read_text(encoding="utf-8") == damaged_content
        )


class TestPersistTranscriptAtomicity:
    async def test_persist_rewrites_content_without_temp_leftovers(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        await _append(manager, "user", "old entry")

        await manager.persist_transcript(
            SESSION_KEY,
            [
                {"role": "user", "content": "new first"},
                {"role": "assistant", "content": "new second"},
            ],
        )

        session = await manager.get_or_create(SESSION_KEY)
        transcript_path = manager._get_transcript_path(session)
        entries = await manager.load_transcript(SESSION_KEY)
        assert [entry.content for entry in entries] == ["new first", "new second"]
        leftovers = [
            path
            for path in transcript_path.parent.glob(transcript_path.name + "*")
            if path != transcript_path
        ]
        assert leftovers == []
