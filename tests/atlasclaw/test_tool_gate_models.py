# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolGateDecision,
    ToolPolicyMode,
)


def test_tool_gate_decision_defaults_to_direct_answer() -> None:
    decision = ToolGateDecision(reason="stable knowledge")
    assert decision.policy is ToolPolicyMode.ANSWER_DIRECT
    assert decision.needs_tool is False
    assert decision.suggested_tool_classes == []


def test_capability_match_result_tracks_missing_capabilities() -> None:
    result = CapabilityMatchResult(
        resolved_policy=ToolPolicyMode.MUST_USE_TOOL,
        tool_candidates=[],
        missing_capabilities=["web_search"],
        reason="live data required",
    )
    assert result.missing_capabilities == ["web_search"]
