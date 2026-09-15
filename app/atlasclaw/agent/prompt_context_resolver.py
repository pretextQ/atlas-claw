# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Session-aware prompt context resolver with budget controls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DYNAMIC_CONTEXT_CHARS_PER_TOKEN = 4
DYNAMIC_TOTAL_BUDGET_RATIO = 0.12
DYNAMIC_PER_FILE_BUDGET_RATIO = 0.35
DYNAMIC_TOTAL_BUDGET_MIN_CHARS = 1_500
DYNAMIC_PER_FILE_BUDGET_MIN_CHARS = 600


@dataclass
class ResolvedPromptFile:
    """Resolved prompt file content ready for prompt injection."""

    filename: str
    path: Path
    content: str
    truncated: bool
    skipped_reason: Optional[str] = None


@dataclass
class PromptBudgetDecision:
    """Effective bootstrap budgets for the current run."""

    total_budget: int
    per_file_budget: int
    source: str


@dataclass
class _BootstrapCacheEntry:
    """Cached bootstrap file snapshot keyed by absolute path."""

    mtime_ns: int
    size: int
    raw_content: str


class PromptContextResolver:
    """Resolve bootstrap/context files with session filters and budget limits."""

    INCLUDE_MARKER = "<!-- atlasclaw-session-include:"
    EXCLUDE_MARKER = "<!-- atlasclaw-session-exclude:"

    def __init__(self) -> None:
        self._bootstrap_file_cache: dict[str, _BootstrapCacheEntry] = {}

    def clear_cache(self) -> None:
        """Clear in-memory bootstrap file cache."""
        self._bootstrap_file_cache.clear()

    def resolve_budgets(
        self,
        *,
        configured_total_budget: int,
        configured_per_file_budget: int,
        context_window_tokens: Optional[int],
    ) -> PromptBudgetDecision:
        """Resolve effective budgets using configured caps and context-window size."""
        normalized_total = max(0, int(configured_total_budget or 0))
        normalized_per_file = max(0, int(configured_per_file_budget or 0))
        normalized_context_tokens = (
            int(context_window_tokens)
            if isinstance(context_window_tokens, int) and context_window_tokens > 0
            else 0
        )

        if normalized_context_tokens <= 0:
            return PromptBudgetDecision(
                total_budget=normalized_total,
                per_file_budget=normalized_per_file,
                source="configured",
            )

        dynamic_char_window = normalized_context_tokens * DYNAMIC_CONTEXT_CHARS_PER_TOKEN
        dynamic_total = max(
            DYNAMIC_TOTAL_BUDGET_MIN_CHARS,
            int(dynamic_char_window * DYNAMIC_TOTAL_BUDGET_RATIO),
        )
        if normalized_total > 0:
            dynamic_total = min(dynamic_total, normalized_total)

        dynamic_per_file = max(
            DYNAMIC_PER_FILE_BUDGET_MIN_CHARS,
            int(dynamic_total * DYNAMIC_PER_FILE_BUDGET_RATIO),
        )
        if normalized_per_file > 0:
            dynamic_per_file = min(dynamic_per_file, normalized_per_file)
        dynamic_per_file = min(dynamic_per_file, dynamic_total)

        return PromptBudgetDecision(
            total_budget=max(0, dynamic_total),
            per_file_budget=max(0, dynamic_per_file),
            source="dynamic_context_window",
        )

    def resolve(
        self,
        *,
        workspace: Path,
        filenames: list[str],
        session_key: Optional[str],
        total_budget: int,
        per_file_budget: int,
    ) -> list[ResolvedPromptFile]:
        normalized_total_budget = max(0, int(total_budget or 0))
        normalized_per_file_budget = max(0, int(per_file_budget or 0))
        remaining_budget = normalized_total_budget
        resolved: list[ResolvedPromptFile] = []

        for filename in filenames:
            file_path = workspace / filename
            if not file_path.exists():
                continue
            raw_content = self._load_file_content(file_path)
            if raw_content is None:
                continue

            content = self._strip_control_markers(raw_content)
            include_tokens = self._read_marker_tokens(raw_content, self.INCLUDE_MARKER)
            exclude_tokens = self._read_marker_tokens(raw_content, self.EXCLUDE_MARKER)
            if not self._is_file_allowed(
                session_key=session_key or "",
                include_tokens=include_tokens,
                exclude_tokens=exclude_tokens,
            ):
                continue

            truncated = False
            if normalized_per_file_budget > 0 and len(content) > normalized_per_file_budget:
                content = content[:normalized_per_file_budget]
                truncated = True

            if normalized_total_budget > 0:
                if remaining_budget <= 0:
                    break
                if len(content) > remaining_budget:
                    content = content[:remaining_budget]
                    truncated = True
                remaining_budget -= len(content)

            if not content:
                continue
            resolved.append(
                ResolvedPromptFile(
                    filename=filename,
                    path=file_path,
                    content=content,
                    truncated=truncated,
                )
            )
        return resolved

    def _load_file_content(self, file_path: Path) -> Optional[str]:
        cache_key = self._cache_key_for_path(file_path)
        try:
            stat = file_path.stat()
        except Exception:
            self._bootstrap_file_cache.pop(cache_key, None)
            return None

        mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
        size = int(stat.st_size)
        cached = self._bootstrap_file_cache.get(cache_key)
        if cached and cached.mtime_ns == mtime_ns and cached.size == size:
            return cached.raw_content

        try:
            raw_content = self._read_file_from_disk(file_path)
        except Exception:
            self._bootstrap_file_cache.pop(cache_key, None)
            return None

        self._bootstrap_file_cache[cache_key] = _BootstrapCacheEntry(
            mtime_ns=mtime_ns,
            size=size,
            raw_content=raw_content,
        )
        return raw_content

    @staticmethod
    def _read_file_from_disk(file_path: Path) -> str:
        return file_path.read_text(encoding="utf-8")

    @staticmethod
    def _cache_key_for_path(file_path: Path) -> str:
        return str(file_path.expanduser().resolve())

    @classmethod
    def _read_marker_tokens(cls, content: str, marker_prefix: str) -> list[str]:
        tokens: list[str] = []
        for line in (content or "").splitlines()[:12]:
            stripped = line.strip()
            if not stripped.startswith(marker_prefix):
                continue
            payload = stripped[len(marker_prefix) :]
            payload = payload.split("-->", 1)[0].strip()
            for token in payload.split(","):
                normalized = token.strip()
                if normalized:
                    tokens.append(normalized)
        return tokens

    @classmethod
    def _strip_control_markers(cls, content: str) -> str:
        filtered: list[str] = []
        for line in (content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(cls.INCLUDE_MARKER) or stripped.startswith(cls.EXCLUDE_MARKER):
                continue
            filtered.append(line)
        return "\n".join(filtered).strip()

    @staticmethod
    def _is_file_allowed(
        *,
        session_key: str,
        include_tokens: list[str],
        exclude_tokens: list[str],
    ) -> bool:
        lowered_key = (session_key or "").lower()
        if include_tokens:
            include_match = any(token.lower() in lowered_key for token in include_tokens)
            if not include_match:
                return False
        if exclude_tokens:
            exclude_match = any(token.lower() in lowered_key for token in exclude_tokens)
            if exclude_match:
                return False
        return True
