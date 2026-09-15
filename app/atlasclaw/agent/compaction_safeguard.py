# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Safeguard helpers for compaction summary quality."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MAX_TOOL_FAILURES = 8
MAX_TOOL_FAILURE_CHARS = 240
MAX_CRITICAL_HISTORY_ITEMS = 6
MAX_CRITICAL_HISTORY_CHARS = 280
DEFAULT_WORKSPACE_SECTIONS: tuple[str, ...] = ("Session Startup", "Red Lines")
DEFAULT_WORKSPACE_RULES_MAX_CHARS = 2_000


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - 3)]}..."


def collect_tool_failures(messages: list[dict]) -> list[str]:
    """Collect compact tool-failure lines from historical messages."""
    failures: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if str(message.get("role", "")).strip().lower() != "tool":
            continue
        metadata = message.get("metadata", {})
        is_error = False
        if isinstance(metadata, dict):
            is_error = bool(metadata.get("is_error")) or str(metadata.get("status", "")).lower() == "error"
        if not is_error:
            continue
        tool_call_id = str(message.get("tool_call_id", "")).strip()
        if tool_call_id and tool_call_id in seen:
            continue
        if tool_call_id:
            seen.add(tool_call_id)
        tool_name = str(message.get("tool_name", "") or message.get("name", "")).strip() or "tool"
        content = str(message.get("content", "") or "")
        summary = _truncate_text(content, MAX_TOOL_FAILURE_CHARS) or "failed (no output)"
        failures.append(f"- {tool_name}: {summary}")
        if len(failures) >= MAX_TOOL_FAILURES:
            break
    return failures


def collect_critical_history(messages: list[dict]) -> list[str]:
    """Collect concise critical history snippets for safeguard summaries."""
    critical: list[str] = []
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", "") or "")
        compact = _truncate_text(content, MAX_CRITICAL_HISTORY_CHARS)
        if not compact:
            continue
        critical.append(f"- [{role}] {compact}")
        if len(critical) >= MAX_CRITICAL_HISTORY_ITEMS:
            break
    return critical


def _extract_markdown_sections(markdown: str, section_titles: list[str]) -> list[str]:
    if not markdown.strip() or not section_titles:
        return []
    section_lookup = {title.strip().lower() for title in section_titles if title.strip()}
    if not section_lookup:
        return []

    lines = markdown.splitlines()
    sections: list[str] = []
    collecting = False
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            if heading in section_lookup:
                if current:
                    sections.append("\n".join(current).strip())
                    current = []
                collecting = True
                current.append(line)
                continue
            if collecting:
                if current:
                    sections.append("\n".join(current).strip())
                    current = []
                collecting = False
            continue
        if collecting:
            current.append(line)

    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section.strip()]


def load_workspace_critical_rules(
    *,
    workspace_path: Optional[str],
    section_titles: tuple[str, ...] = DEFAULT_WORKSPACE_SECTIONS,
    max_chars: int = DEFAULT_WORKSPACE_RULES_MAX_CHARS,
) -> str:
    if not workspace_path:
        return ""
    agents_path = Path(workspace_path).expanduser().resolve() / "AGENTS.md"
    if not agents_path.exists():
        return ""
    try:
        content = agents_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    sections = _extract_markdown_sections(content, list(section_titles))
    if not sections:
        return ""
    combined = "\n\n".join(sections).strip()
    if not combined:
        return ""
    if max_chars > 0 and len(combined) > max_chars:
        return f"{combined[: max_chars - 3]}..."
    return combined


def build_safeguarded_summary(
    *,
    messages: list[dict],
    base_summary: str,
    workspace_critical_rules: str = "",
) -> str:
    """Append critical history and tool-failure sections to base compaction summary."""
    summary = (base_summary or "").strip()
    sections: list[str] = [summary] if summary else []

    critical_lines = collect_critical_history(messages)
    if critical_lines:
        sections.append("## Critical History")
        sections.extend(critical_lines)

    failure_lines = collect_tool_failures(messages)
    if failure_lines:
        sections.append("## Tool Failures")
        sections.extend(failure_lines)

    workspace_rules = (workspace_critical_rules or "").strip()
    if workspace_rules:
        sections.append("## Workspace Critical Rules")
        sections.append(workspace_rules)

    return "\n".join(part for part in sections if part).strip()
