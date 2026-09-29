# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-08 regression tests: compaction correctness.

Covers: tool_calls/tool_result pairs are not split by the compaction
boundary (F-0012), None tool_calls no longer crash token estimation
(F-0013), short-but-huge transcripts really get compacted (F-0014),
summarizer failures are logged, soft_trim_enabled gates pruning, and tool
result pruning matches every TOOL_RESULT_ROLES spelling.
"""

from __future__ import annotations

import logging

import pytest

from app.atlasclaw.agent.compaction import (
    COMPACTION_SUMMARY_PREFIX,
    CompactionConfig,
    CompactionPipeline,
    TOOL_RESULT_ROLES,
)


def _make_pipeline(**config_overrides) -> CompactionPipeline:
    async def _summarizer(batch: list[dict]) -> str:
        return f"summary(len={len(batch)})"

    base = dict(
        context_window=2000,
        reserve_tokens_floor=200,
        soft_threshold_tokens=100,
        keep_recent_turns=1,
        max_history_share=1.0,
        safeguard_enabled=False,
    )
    base.update(config_overrides)
    config = CompactionConfig(**base)
    return CompactionPipeline(config, summarizer=_summarizer)


class TestSplitBoundary:
    def test_tool_call_and_result_stay_on_the_recent_side(self):
        """The retained side must not begin with an orphan tool result."""
        pipeline = _make_pipeline(keep_recent_turns=1)
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result 1"},
            {"role": "assistant", "content": "a2", "tool_calls": [{"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c2", "content": "result 2"},
            {"role": "user", "content": "u2"},
        ]

        system_prompt, recent_messages, to_compress = pipeline._split_for_compaction(messages)

        assert recent_messages, "recent side must not be empty"
        first_role = str(recent_messages[0].get("role") or "").lower()
        assert first_role not in TOOL_RESULT_ROLES
        # The assistant that owns the retained tool result is retained too.
        retained_ids = {
            call.get("id")
            for msg in recent_messages
            for call in (msg.get("tool_calls") or [])
        }
        for msg in recent_messages:
            call_id = msg.get("tool_call_id")
            if str(msg.get("role") or "").lower() in TOOL_RESULT_ROLES and call_id:
                assert call_id in retained_ids


class TestTokenEstimation:
    def test_none_tool_calls_do_not_crash_estimation(self):
        """A tool_calls key with value None must count as 'no calls' (F-0013)."""
        pipeline = _make_pipeline()
        estimate = pipeline.estimate_tokens([
            {"role": "assistant", "content": "hello", "tool_calls": None},
        ])
        assert estimate == len("hello") // 4


class TestShortButHugeCompaction:
    def _huge_messages(self) -> list[dict]:
        huge = "x" * 120_000
        return [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": huge},
            {"role": "assistant", "content": huge},
            {"role": "user", "content": "answer me"},
        ]

    def test_should_compact_flags_short_huge_transcript(self):
        """should_compact fires on a few huge messages (F-0014 precondition)."""
        pipeline = _make_pipeline(keep_recent_turns=3)
        messages = self._huge_messages()
        assert pipeline.should_compact(messages) is True

    @pytest.mark.asyncio
    async def test_short_huge_transcript_is_compacted(self):
        pipeline = _make_pipeline(keep_recent_turns=3)
        messages = self._huge_messages()

        compacted = await pipeline.compact(messages)

        assert compacted != messages
        assert any(
            str(m.get("content", "")).startswith(COMPACTION_SUMMARY_PREFIX)
            for m in compacted
        )
        # The most recent exchange stays verbatim.
        assert compacted[-1]["content"] == "answer me"

    @pytest.mark.asyncio
    async def test_short_small_transcript_stays_untouched(self):
        pipeline = _make_pipeline(keep_recent_turns=3)
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ]
        assert await pipeline.compact(messages) == messages


class TestSummarizerFailureLogging:
    @pytest.mark.asyncio
    async def test_summary_failure_is_logged_not_silent(self, caplog):
        async def _boom(batch: list[dict]) -> str:
            raise RuntimeError("summarizer exploded")

        pipeline = CompactionPipeline(
            CompactionConfig(
                context_window=2000,
                reserve_tokens_floor=200,
                soft_threshold_tokens=100,
                keep_recent_turns=1,
                max_history_share=1.0,
                safeguard_enabled=False,
            ),
            summarizer=_boom,
        )
        messages = [
            {"role": "user", "content": f"message {i}" * 20} for i in range(12)
        ]

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.agent.compaction"):
            compacted = await pipeline.compact(messages)

        assert compacted == messages
        assert any("Summary generation failed" in r.getMessage() for r in caplog.records)


class TestPruneToolResults:
    def _big_tool_message(self, role: str = "tool") -> dict:
        return {"role": role, "tool_call_id": "c1", "content": "y" * 50_000}

    @staticmethod
    def _messages_with_old_tool_result(msg: dict) -> list[dict]:
        # The tool result sits before more than keep_last_assistants (3)
        # assistant messages, so it falls outside the "recent" window and
        # pruning applies.
        return [
            msg,
            *[{"role": "assistant", "content": f"old {i}"} for i in range(4)],
        ]

    def test_soft_trim_disabled_keeps_content(self):
        """soft_trim_enabled=False must not prune (config was dead before)."""
        pipeline = _make_pipeline(hard_clear_threshold=100, soft_trim_enabled=False)
        pruned = pipeline.prune_tool_results(
            self._messages_with_old_tool_result(self._big_tool_message()), mode="soft"
        )
        assert pruned[0]["content"] == "y" * 50_000

    def test_soft_trim_enabled_trims(self):
        pipeline = _make_pipeline(hard_clear_threshold=100, soft_trim_enabled=True)
        pruned = pipeline.prune_tool_results(
            self._messages_with_old_tool_result(self._big_tool_message()), mode="soft"
        )
        assert len(pruned[0]["content"]) < 50_000
        assert "Original size" in pruned[0]["content"]

    @pytest.mark.parametrize("role", ["tool", "toolresult", "tool_result"])
    def test_all_tool_result_roles_are_pruned(self, role):
        """Role matching must cover every TOOL_RESULT_ROLES spelling."""
        pipeline = _make_pipeline(hard_clear_threshold=100, soft_trim_enabled=True)
        pruned = pipeline.prune_tool_results(
            self._messages_with_old_tool_result(self._big_tool_message(role=role)),
            mode="soft",
        )
        assert len(pruned[0]["content"]) < 50_000
