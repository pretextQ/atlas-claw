# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-16 regression tests: memory section parsing consistency.

Covers: entries whose text begins with '#' or '*' are readable back from a
section (F-0045), and the write-lock registry is keyed on the canonical
path so different spellings of one file share a lock (F-0046).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.atlasclaw.memory.manager import MemoryManager, _write_lock_for


class TestSectionLineExtraction:
    def test_starred_entry_is_read_back(self):
        content = (
            "# Long-term Memory\n\n"
            "## General\n\n"
            "*starred entry\n"
            "plain entry\n\n"
            "## Other\n\n"
            "other entry\n"
        )
        lines = [text for _, text in MemoryManager.extract_section_lines(content, "general")]
        assert lines == ["*starred entry", "plain entry"]

    def test_hash_prefixed_entry_is_read_back(self):
        content = (
            "# Long-term Memory\n\n"
            "## General\n\n"
            "#tag entry\n"
            "plain entry\n"
        )
        lines = [text for _, text in MemoryManager.extract_section_lines(content, "general")]
        assert lines == ["#tag entry", "plain entry"]

    def test_real_headings_are_still_skipped(self):
        content = (
            "# Long-term Memory\n\n"
            "## General\n\n"
            "### Sub heading\n"
            "kept entry\n\n"
            "## Next\n\n"
            "not in general\n"
        )
        lines = [text for _, text in MemoryManager.extract_section_lines(content, "general")]
        assert lines == ["kept entry"]

    def test_emphasis_decoration_is_skipped(self):
        content = "## General\n\n* * *\n*emphasis decoration*\nkept entry\n"
        lines = [text for _, text in MemoryManager.extract_section_lines(content, "general")]
        assert lines == ["kept entry"]

    def test_section_matching_is_case_insensitive(self):
        content = "## General\n\nentry\n"
        assert [t for _, t in MemoryManager.extract_section_lines(content, "GENERAL")] == ["entry"]


class TestWriteLockKeyNormalization:
    def test_relative_and_absolute_spellings_share_one_lock(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        relative = Path("memory/MEMORY.md")
        absolute = tmp_path / "memory" / "MEMORY.md"

        assert _write_lock_for(relative) is _write_lock_for(absolute)

    def test_drive_letter_case_shares_one_lock(self, tmp_path):
        first = _write_lock_for(tmp_path / "MEMORY.md")
        second = _write_lock_for(Path(str(tmp_path).swapcase()) / "MEMORY.md")
        assert first is second

    def test_distinct_files_get_distinct_locks(self, tmp_path):
        first = _write_lock_for(tmp_path / "a" / "MEMORY.md")
        second = _write_lock_for(tmp_path / "b" / "MEMORY.md")
        assert first is not second
