# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig
from app.atlasclaw.agent.prompt_context_resolver import PromptContextResolver


def test_resolver_respects_total_budget(tmp_path: Path):
    (tmp_path / "A.md").write_text("A" * 120, encoding="utf-8")
    (tmp_path / "B.md").write_text("B" * 120, encoding="utf-8")
    resolver = PromptContextResolver()

    resolved = resolver.resolve(
        workspace=tmp_path,
        filenames=["A.md", "B.md"],
        session_key="agent:main:user:u1:web:dm:u1",
        total_budget=100,
        per_file_budget=80,
    )

    assert len(resolved) == 2
    assert len(resolved[0].content) == 80
    assert len(resolved[1].content) == 20
    assert sum(len(item.content) for item in resolved) <= 100


def test_resolver_filters_by_session_markers(tmp_path: Path):
    (tmp_path / "match.md").write_text(
        "<!-- atlasclaw-session-include: telegram -->\nMATCHED",
        encoding="utf-8",
    )
    (tmp_path / "skip.md").write_text(
        "<!-- atlasclaw-session-exclude: group -->\nSHOULD_SKIP",
        encoding="utf-8",
    )

    resolver = PromptContextResolver()
    matched = resolver.resolve(
        workspace=tmp_path,
        filenames=["match.md", "skip.md"],
        session_key="agent:main:user:u1:telegram:dm:u1",
        total_budget=1000,
        per_file_budget=1000,
    )
    assert [item.filename for item in matched] == ["match.md", "skip.md"]

    mismatched = resolver.resolve(
        workspace=tmp_path,
        filenames=["match.md", "skip.md"],
        session_key="agent:main:user:u1:web:group:g-1",
        total_budget=1000,
        per_file_budget=1000,
    )
    assert [item.filename for item in mismatched] == []


@dataclass
class _SessionStub:
    session_key: str


def test_prompt_builder_bootstrap_uses_resolver_budget(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("AG" * 200, encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("SO" * 200, encoding="utf-8")
    builder = PromptBuilder(
        PromptBuilderConfig(
            workspace_path=str(tmp_path),
            bootstrap_max_chars=120,
            bootstrap_total_max_chars=180,
        )
    )

    rendered = builder._build_bootstrap(session=_SessionStub("agent:main:user:u1:web:dm:u1"))

    assert "## Project Context" in rendered
    assert "### AGENTS.md" in rendered
    assert "Truncated by prompt budget" in rendered
    # total budget should cap aggregate payload (with section headers overhead)
    assert len(rendered) < 800


def test_prompt_builder_bootstrap_budget_scales_with_context_window(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("A" * 6000, encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("S" * 6000, encoding="utf-8")
    builder = PromptBuilder(
        PromptBuilderConfig(
            workspace_path=str(tmp_path),
            bootstrap_max_chars=4000,
            bootstrap_total_max_chars=10000,
        )
    )

    wide = builder._build_bootstrap(
        session=_SessionStub("agent:main:user:u1:web:dm:u1"),
        context_window_tokens=128000,
    )
    narrow = builder._build_bootstrap(
        session=_SessionStub("agent:main:user:u1:web:dm:u1"),
        context_window_tokens=2000,
    )

    assert len(wide) > len(narrow)
    assert "### AGENTS.md" in wide
    assert "### AGENTS.md" in narrow


def test_prompt_builder_collects_bootstrap_warnings(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("READY", encoding="utf-8")
    builder = PromptBuilder(PromptBuilderConfig(workspace_path=str(tmp_path)))

    builder._build_bootstrap(session=_SessionStub("agent:main:user:u1:web:dm:u1"))
    warnings = builder.consume_warnings()

    assert any("missing bootstrap file" in item.lower() for item in warnings)
    assert builder.consume_warnings() == []


def test_resolver_bootstrap_cache_reuses_unchanged_file(tmp_path: Path):
    file_path = tmp_path / "AGENTS.md"
    file_path.write_text("BOOTSTRAP-V1", encoding="utf-8")
    resolver = PromptContextResolver()
    read_calls = 0
    original_reader = resolver._read_file_from_disk

    def _counted_reader(path: Path) -> str:
        nonlocal read_calls
        read_calls += 1
        return original_reader(path)

    resolver._read_file_from_disk = _counted_reader  # type: ignore[assignment]

    first = resolver.resolve(
        workspace=tmp_path,
        filenames=["AGENTS.md"],
        session_key="agent:main:user:u1:web:dm:u1",
        total_budget=1000,
        per_file_budget=1000,
    )
    second = resolver.resolve(
        workspace=tmp_path,
        filenames=["AGENTS.md"],
        session_key="agent:main:user:u1:web:dm:u1",
        total_budget=1000,
        per_file_budget=1000,
    )

    assert first and second
    assert first[0].content == "BOOTSTRAP-V1"
    assert second[0].content == "BOOTSTRAP-V1"
    assert read_calls == 1


def test_resolver_bootstrap_cache_invalidates_on_file_change(tmp_path: Path):
    file_path = tmp_path / "AGENTS.md"
    file_path.write_text("BOOTSTRAP-V1", encoding="utf-8")
    resolver = PromptContextResolver()
    read_calls = 0
    original_reader = resolver._read_file_from_disk

    def _counted_reader(path: Path) -> str:
        nonlocal read_calls
        read_calls += 1
        return original_reader(path)

    resolver._read_file_from_disk = _counted_reader  # type: ignore[assignment]

    first = resolver.resolve(
        workspace=tmp_path,
        filenames=["AGENTS.md"],
        session_key="agent:main:user:u1:web:dm:u1",
        total_budget=1000,
        per_file_budget=1000,
    )
    assert first and first[0].content == "BOOTSTRAP-V1"
    assert read_calls == 1

    file_path.write_text("BOOTSTRAP-V2-UPDATED", encoding="utf-8")
    stat = file_path.stat()
    os.utime(file_path, (stat.st_atime, stat.st_mtime + 1.0))

    second = resolver.resolve(
        workspace=tmp_path,
        filenames=["AGENTS.md"],
        session_key="agent:main:user:u1:web:dm:u1",
        total_budget=1000,
        per_file_budget=1000,
    )

    assert second and second[0].content == "BOOTSTRAP-V2-UPDATED"
    assert read_calls == 2
