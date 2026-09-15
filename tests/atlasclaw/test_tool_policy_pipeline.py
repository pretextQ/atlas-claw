# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Unit tests for per-turn tool policy pipeline."""

from __future__ import annotations

from app.atlasclaw.tools.policy_pipeline import (
    ToolPolicyLayer,
    ToolPolicyPipeline,
    build_ordered_policy_layers,
)


def _tools() -> list[dict]:
    return [
        {"name": "web_search", "description": "search", "capability_class": "web_search"},
        {"name": "web_fetch", "description": "fetch", "capability_class": "web_fetch"},
        {"name": "cmp_list_pending", "description": "cmp", "capability_class": "provider:smartcmp"},
        {"name": "cmp_get_ticket", "description": "cmp", "capability_class": "provider:smartcmp"},
        {"name": "session_status", "description": "status"},
    ]


def test_pipeline_applies_deny_before_allow_per_layer() -> None:
    pipeline = ToolPolicyPipeline(
        tools=_tools(),
        group_map={"group:web": ["web_search", "web_fetch"]},
    )
    result = pipeline.run(
        [
            ToolPolicyLayer(name="layer-1", allow=["group:web"], deny=["web_fetch"]),
        ]
    )
    assert result.tool_names == ["web_search"]


def test_pipeline_allow_empty_keeps_set_after_deny() -> None:
    pipeline = ToolPolicyPipeline(tools=_tools(), group_map={})
    result = pipeline.run([ToolPolicyLayer(name="layer-1", deny=["web_fetch"])])
    assert "web_search" in result.tool_names
    assert "web_fetch" not in result.tool_names


def test_pipeline_supports_glob_and_alias_expansion() -> None:
    pipeline = ToolPolicyPipeline(
        tools=_tools(),
        group_map={},
        aliases={"provider:smartcmp": ["cmp_list_pending", "cmp_get_ticket"]},
    )
    result = pipeline.run(
        [
            ToolPolicyLayer(name="layer-1", allow=["cmp_*"]),
            ToolPolicyLayer(name="layer-2", allow=["provider:smartcmp"], deny=["cmp_get_ticket"]),
        ]
    )
    assert result.tool_names == ["cmp_list_pending"]


def test_build_ordered_policy_layers_reads_provider_agent_channel_session() -> None:
    policy = {
        "profile": {"allow": ["group:web"]},
        "global": {"deny": ["web_fetch"]},
        "by_provider": {"smartcmp": {"allow": ["provider:smartcmp"]}},
        "by_agent": {"main": {"allow": ["cmp_*"]}},
        "channel": {"web": {"allow": ["cmp_list_pending"]}},
        "by_session": {"s-1": {"deny": ["cmp_get_ticket"]}},
    }
    layers = build_ordered_policy_layers(
        policy=policy,
        provider_type="smartcmp",
        agent_id="main",
        channel="web",
        session_key="s-1",
    )
    assert [layer.name for layer in layers] == [
        "base_profile",
        "global",
        "provider",
        "agent",
        "channel_session",
        "session",
    ]
    assert layers[0].allow == ["group:web"]
    assert layers[1].deny == ["web_fetch"]
    assert layers[2].allow == ["provider:smartcmp"]
    assert layers[3].allow == ["cmp_*"]
    assert layers[4].allow == ["cmp_list_pending"]
    assert layers[5].deny == ["cmp_get_ticket"]
