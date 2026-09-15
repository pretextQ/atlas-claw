# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.agent.compaction_safeguard import build_safeguarded_summary
from app.atlasclaw.agent.compaction_safeguard import load_workspace_critical_rules
from app.atlasclaw.agent.context_pruning import (
    ContextPruningSettings,
    HardClearConfig,
    SoftTrimConfig,
    should_apply_context_pruning,
    prune_context_messages,
)


def _base_messages_with_large_tool(content: str) -> list[dict]:
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "tool_name": "web_fetch", "content": content},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]


def test_soft_trim_tool_result_keeps_head_tail():
    original = "A" * 2000 + "B" * 2000
    settings = ContextPruningSettings(
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=400, tail_chars=400),
    )
    pruned = prune_context_messages(
        messages=_base_messages_with_large_tool(original),
        settings=settings,
        context_window_tokens=1200,
    )

    tool_message = next(msg for msg in pruned if msg.get("role") == "tool")
    text = str(tool_message.get("content", ""))
    assert "Tool result trimmed" in text
    assert text.startswith("A" * 50)
    assert "B" * 50 in text
    assert len(text) < len(original)


def test_pruning_keeps_recent_assistant_tail():
    messages = _base_messages_with_large_tool("X" * 6000)
    settings = ContextPruningSettings(keep_last_assistants=3)
    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=1000,
    )

    original_tail = [msg for msg in messages if msg.get("role") == "assistant"][-3:]
    pruned_tail = [msg for msg in pruned if msg.get("role") == "assistant"][-3:]
    assert pruned_tail == original_tail


def test_pruning_preserves_failed_tool_outputs():
    messages = _base_messages_with_large_tool("Y" * 9000)
    messages[2]["metadata"] = {"is_error": True}
    settings = ContextPruningSettings()

    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=900,
    )

    tool_message = next(msg for msg in pruned if msg.get("role") == "tool")
    assert tool_message.get("content") == "Y" * 9000


def test_hard_clear_replaces_payload_when_pressure_is_high():
    large_messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
    ]
    for idx in range(6):
        large_messages.append({"role": "tool", "tool_name": f"t{idx}", "content": "Z" * 22000})
        large_messages.append({"role": "assistant", "content": f"a{idx}"})
    large_messages.extend(
        [
            {"role": "user", "content": "latest user"},
            {"role": "assistant", "content": "latest assistant"},
            {"role": "assistant", "content": "latest assistant 2"},
            {"role": "assistant", "content": "latest assistant 3"},
        ]
    )

    settings = ContextPruningSettings(
        keep_last_assistants=3,
        hard_clear_ratio=0.5,
        min_prunable_tool_chars=1_000,
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=300, tail_chars=300),
        hard_clear=HardClearConfig(
            enabled=True,
            placeholder="[Tool result cleared to save context space]",
        ),
    )
    pruned = prune_context_messages(
        messages=large_messages,
        settings=settings,
        context_window_tokens=2000,
    )

    cleared_tools = [
        msg for msg in pruned if msg.get("role") == "tool" and msg.get("content") == settings.hard_clear.placeholder
    ]
    assert cleared_tools


def test_safeguard_extracts_tool_failures_into_summary():
    messages = [
        {"role": "user", "content": "请帮我查天气"},
        {"role": "assistant", "content": "我开始查询"},
        {
            "role": "tool",
            "tool_name": "web_search",
            "content": "timeout",
            "metadata": {"status": "error"},
            "tool_call_id": "c1",
        },
    ]
    safeguarded = build_safeguarded_summary(messages=messages, base_summary="Base summary")
    assert "## Critical History" in safeguarded
    assert "## Tool Failures" in safeguarded
    assert "web_search" in safeguarded


def test_pruning_respects_tool_allow_deny_patterns():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "tool_name": "web_fetch", "content": "A" * 9000},
        {"role": "tool", "tool_name": "memory_search", "content": "B" * 9000},
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
        {"role": "assistant", "content": "a3"},
    ]
    settings = ContextPruningSettings(
        tools_allow=["web_*"],
        tools_deny=["web_fetch"],
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=300, tail_chars=300),
    )
    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=800,
    )
    web_fetch = next(msg for msg in pruned if msg.get("tool_name") == "web_fetch")
    memory_search = next(msg for msg in pruned if msg.get("tool_name") == "memory_search")
    assert web_fetch.get("content") == "A" * 9000
    assert memory_search.get("content") == "B" * 9000


def test_pruning_mode_off_disables_pruning():
    original = _base_messages_with_large_tool("X" * 9000)
    settings = ContextPruningSettings(mode="off")
    pruned = prune_context_messages(
        messages=original,
        settings=settings,
        context_window_tokens=600,
    )
    assert pruned == original


def test_cache_ttl_pruning_applies_once_per_window():
    class _Session:
        pass

    session = _Session()
    settings = ContextPruningSettings(mode="cache-ttl", ttl_ms=5_000)

    first = should_apply_context_pruning(settings=settings, session=session, now_ms=10_000)
    second = should_apply_context_pruning(settings=settings, session=session, now_ms=12_000)
    third = should_apply_context_pruning(settings=settings, session=session, now_ms=16_000)

    assert first is True
    assert second is False
    assert third is True


def test_pruning_accepts_tool_result_role_alias():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
        {"role": "toolResult", "tool_name": "web_fetch", "content": "C" * 5000},
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
        {"role": "assistant", "content": "a3"},
    ]
    settings = ContextPruningSettings(
        soft_trim=SoftTrimConfig(max_chars=1200, head_chars=300, tail_chars=300),
    )
    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=600,
    )

    tool_message = next(msg for msg in pruned if str(msg.get("role", "")).lower() == "toolresult")
    assert "Tool result trimmed" in str(tool_message.get("content", ""))


def test_hard_clear_stops_once_ratio_recovers():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
    ]
    for idx in range(4):
        messages.append({"role": "tool", "tool_name": f"tool_{idx}", "content": "Q" * 10000})
        messages.append({"role": "assistant", "content": f"a{idx}"})
    messages.extend(
        [
            {"role": "assistant", "content": "tail-a1"},
            {"role": "assistant", "content": "tail-a2"},
            {"role": "assistant", "content": "tail-a3"},
        ]
    )

    settings = ContextPruningSettings(
        keep_last_assistants=3,
        hard_clear_ratio=0.5,
        min_prunable_tool_chars=1_000,
        soft_trim=SoftTrimConfig(max_chars=20_000, head_chars=100, tail_chars=100),
        hard_clear=HardClearConfig(
            enabled=True,
            placeholder="[Tool result cleared to save context space]",
        ),
    )
    pruned = prune_context_messages(
        messages=messages,
        settings=settings,
        context_window_tokens=7_500,
    )

    tool_payloads = [msg.get("content", "") for msg in pruned if msg.get("role") == "tool"]
    cleared_count = sum(1 for payload in tool_payloads if payload == settings.hard_clear.placeholder)
    untouched_count = sum(1 for payload in tool_payloads if payload == "Q" * 10000)

    assert cleared_count > 0
    assert untouched_count > 0


def test_safeguard_loads_workspace_critical_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# Intro\nIgnore\n\n## Session Startup\nKeep startup rule.\n\n## Red Lines\nNever do X.\n",
        encoding="utf-8",
    )
    rules = load_workspace_critical_rules(workspace_path=str(tmp_path))

    assert "## Session Startup" in rules
    assert "## Red Lines" in rules
    assert "Ignore" not in rules
