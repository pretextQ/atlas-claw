# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
MemoryManager 路径隔离单元测试

涵盖：不同 user_id 存储在不同子目录、旧数据迁移到 memory/default/。
"""

from __future__ import annotations

import asyncio

import pytest
from pathlib import Path

from app.atlasclaw.memory.manager import MemoryManager


class TestMemoryIsolation:

    @pytest.mark.asyncio
    async def test_long_term_memory_stored_in_user_subdir(self, tmp_path):
        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()
        await manager.write_long_term("Test memory content", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        assert alice_dir.exists()
        assert (alice_dir / "MEMORY.md").exists()

    @pytest.mark.asyncio
    async def test_long_term_path_in_user_subdir(self, tmp_path):
        manager = MemoryManager(workspace=str(tmp_path), user_id="u-bob")
        expected_path = tmp_path / "users" / "u-bob" / "memory" / "MEMORY.md"
        assert manager.long_term_path == expected_path

    @pytest.mark.asyncio
    async def test_different_users_use_separate_directories(self, tmp_path):
        mgr_alice = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        mgr_bob = MemoryManager(workspace=str(tmp_path), user_id="u-bob")

        await mgr_alice.ensure_dirs()
        await mgr_bob.ensure_dirs()
        await mgr_alice.write_long_term("Alice memory", source="test")
        await mgr_bob.write_long_term("Bob memory", source="test")

        alice_dir = tmp_path / "users" / "u-alice" / "memory"
        bob_dir = tmp_path / "users" / "u-bob" / "memory"

        assert alice_dir.exists()
        assert bob_dir.exists()
        assert alice_dir != bob_dir
        assert "Alice memory" in (alice_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "Alice memory" not in (bob_dir / "MEMORY.md").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_legacy_date_scoped_files_are_not_migrated(self, tmp_path):
        """Date-scoped legacy files are not part of the long-term memory contract."""
        legacy_dir = tmp_path / "memory"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "2025-01-15.md").write_text("# Legacy memory\n\nOld content\n",
                                                    encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="default")
        await manager.ensure_dirs()

        default_dir = tmp_path / "users" / "default" / "memory"
        assert default_dir.exists()
        assert not (default_dir / "2025-01-15.md").exists()
        assert (legacy_dir / "2025-01-15.md").exists()

    @pytest.mark.asyncio
    async def test_legacy_user_subdir_is_not_migrated(self, tmp_path):
        """Legacy user directories are not read or moved by the long-term manager."""
        legacy_user_dir = tmp_path / "memory" / "u-alice"
        legacy_user_dir.mkdir(parents=True)
        (legacy_user_dir / "MEMORY.md").write_text("# Legacy memory\n", encoding="utf-8")

        manager = MemoryManager(workspace=str(tmp_path), user_id="u-alice")
        await manager.ensure_dirs()

        new_user_dir = tmp_path / "users" / "u-alice" / "memory"
        assert not (new_user_dir / "MEMORY.md").exists()
        assert (legacy_user_dir / "MEMORY.md").exists()


class TestConcurrentMemoryWrites:
    """Per-request managers must share one write lock per memory file."""

    def test_for_user_instances_share_the_write_lock(self, tmp_path: Path) -> None:
        template = MemoryManager(workspace=str(tmp_path), user_id="default")
        first = template.for_user("u1")
        second = template.for_user("u1")

        assert first._write_lock is second._write_lock
        # Different users keep independent locks.
        other_user = template.for_user("u2")
        assert other_user._write_lock is not first._write_lock

    def test_concurrent_writes_from_separate_managers_do_not_lose_entries(
        self, tmp_path: Path
    ) -> None:
        """Every concurrent write must survive: no lost updates."""
        template = MemoryManager(workspace=str(tmp_path), user_id="u1")

        async def scenario() -> str:
            managers = [template.for_user("u1") for _ in range(8)]
            await asyncio.gather(
                *(
                    manager.write_long_term(
                        f"concurrent fact {index}",
                        source="test",
                        section="General",
                    )
                    for index, manager in enumerate(managers)
                )
            )
            return managers[0].long_term_path.read_text(encoding="utf-8")

        content = asyncio.run(scenario())

        for index in range(8):
            assert f"concurrent fact {index}" in content

    def test_atomic_write_leaves_no_temp_files(self, tmp_path: Path) -> None:
        manager = MemoryManager(workspace=str(tmp_path), user_id="u1")

        asyncio.run(manager.write_long_term("atomic fact", source="test"))

        memory_dir = manager.long_term_path.parent
        leftovers = list(memory_dir.glob("*.tmp"))
        assert leftovers == []
        assert manager.long_term_path.exists()


class TestMemorySectionHandling:
    """Section headings must stay single-line and case-insensitively unique."""

    @pytest.mark.asyncio
    async def test_section_injection_is_rejected(self, tmp_path: Path) -> None:
        manager = MemoryManager(workspace=str(tmp_path), user_id="u1")

        for hostile in (
            "General\n\n## Injected",
            "General\r\n## Injected",
            "# Heading",
            "## Sub",
            "   ",
            "x" * 200,
        ):
            with pytest.raises(ValueError):
                await manager.write_long_term("payload", source="test", section=hostile)

        assert not manager.long_term_path.exists()

    @pytest.mark.asyncio
    async def test_section_aliases_with_different_case_share_one_section(
        self, tmp_path: Path
    ) -> None:
        manager = MemoryManager(workspace=str(tmp_path), user_id="u1")

        await manager.write_long_term("first fact", source="test", section="General")
        await manager.write_long_term("second fact", source="test", section="general")

        content = manager.long_term_path.read_text(encoding="utf-8")

        assert content.count("## ") == 1
        assert "first fact" in content
        assert "second fact" in content

    @pytest.mark.asyncio
    async def test_replace_section_matches_case_insensitively(self, tmp_path: Path) -> None:
        manager = MemoryManager(workspace=str(tmp_path), user_id="u1")
        await manager.write_long_term("old fact", source="test", section="Preferences")

        await manager.replace_long_term_section(
            ["new fact"],
            source="test",
            section="preferences",
        )

        content = manager.long_term_path.read_text(encoding="utf-8")

        assert content.count("## ") == 1
        assert "new fact" in content
        assert "old fact" not in content


class TestCjkMemorySearch:
    """Chinese memory text must be searchable, and stats must stay consistent."""

    def test_whole_sentence_is_not_one_token(self) -> None:
        from app.atlasclaw.memory.search import HybridSearcher

        searcher = HybridSearcher()
        tokens = searcher._tokenize("用户偏好简洁的中文回答")

        # Bigrams let a sub-phrase query match a longer stored sentence.
        assert "中文" in tokens
        assert "简洁" in tokens
        assert len(tokens) > 3

    @pytest.mark.asyncio
    async def test_chinese_subphrase_query_matches_stored_memory(
        self, tmp_path: Path
    ) -> None:
        from app.atlasclaw.memory.search import HybridSearcher
        from app.atlasclaw.memory.manager import MemoryEntry, MemoryType

        searcher = HybridSearcher()
        searcher.index_sync(
            MemoryEntry(
                id="cjk-1",
                content="用户偏好简洁的中文回答",
                memory_type=MemoryType.LONG_TERM,
                source="test",
            )
        )
        searcher.index_sync(
            MemoryEntry(
                id="en-1",
                content="The user prefers deploy windows on fridays",
                memory_type=MemoryType.LONG_TERM,
                source="test",
            )
        )

        results = await searcher.search("中文回答", top_k=5)

        assert results
        assert results[0].entry.id == "cjk-1"
        assert results[0].text_score > 0

    @pytest.mark.asyncio
    async def test_removed_entries_roll_back_term_statistics(self) -> None:
        from app.atlasclaw.memory.search import HybridSearcher
        from app.atlasclaw.memory.manager import MemoryEntry, MemoryType

        searcher = HybridSearcher()
        for index in range(3):
            searcher.index_sync(
                MemoryEntry(
                    id=f"e{index}",
                    content="deploy window",
                    memory_type=MemoryType.LONG_TERM,
                    source="test",
                )
            )

        assert searcher._term_doc_freq["deploy"] == 3
        for index in range(3):
            assert searcher.remove(f"e{index}") is True

        # Every term statistic is back to empty rather than stale.
        assert searcher._term_doc_freq == {}
        assert searcher._doc_count == 0
        assert searcher._avg_doc_length == 0.0

    @pytest.mark.asyncio
    async def test_reindexing_same_entry_does_not_inflate_frequencies(self) -> None:
        from app.atlasclaw.memory.search import HybridSearcher
        from app.atlasclaw.memory.manager import MemoryEntry, MemoryType

        searcher = HybridSearcher()
        entry = MemoryEntry(
            id="dup",
            content="deploy window",
            memory_type=MemoryType.LONG_TERM,
            source="test",
        )
        searcher.index_sync(entry)
        searcher.index_sync(entry)

        assert searcher._term_doc_freq["deploy"] == 1
        assert searcher._doc_count == 1
