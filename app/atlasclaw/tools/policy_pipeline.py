# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tool policy pipeline for per-turn minimal executable toolset selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Optional


@dataclass
class ToolPolicyLayer:
    """One policy layer in the monotonic allow/deny pipeline."""

    name: str
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class ToolPolicyPipelineResult:
    """Result payload for one pipeline run."""

    tool_names: list[str]
    trace: list[dict[str, Any]]


class ToolPolicyPipeline:
    """Apply ordered allow/deny layers over runtime tool metadata."""

    def __init__(
        self,
        *,
        tools: list[dict[str, Any]],
        group_map: Optional[dict[str, list[str]]] = None,
        aliases: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self._tools = [item for item in tools if isinstance(item, dict) and str(item.get("name", "")).strip()]
        self._tool_names = [str(item["name"]).strip() for item in self._tools]
        self._universe = set(self._tool_names)
        self._group_map = {
            str(name).strip(): [
                str(member).strip()
                for member in (members or [])
                if str(member).strip()
            ]
            for name, members in (group_map or {}).items()
            if str(name).strip()
        }
        self._aliases = {
            str(name).strip(): [
                str(member).strip()
                for member in (members or [])
                if str(member).strip()
            ]
            for name, members in (aliases or {}).items()
            if str(name).strip()
        }

    def run(self, layers: list[ToolPolicyLayer]) -> ToolPolicyPipelineResult:
        current = set(self._universe)
        trace: list[dict[str, Any]] = []

        for layer in layers:
            deny_set = self._expand_patterns(layer.deny)
            after_deny = current - deny_set

            allow_set = self._expand_patterns(layer.allow)
            if allow_set:
                after_allow = after_deny.intersection(allow_set)
                applied_allow = True
            else:
                after_allow = after_deny
                applied_allow = False

            trace.append(
                {
                    "layer": layer.name,
                    "before_count": len(current),
                    "after_deny_count": len(after_deny),
                    "after_allow_count": len(after_allow),
                    "allow": list(layer.allow),
                    "deny": list(layer.deny),
                    "applied_allow": applied_allow,
                }
            )
            current = after_allow

        ordered = [name for name in self._tool_names if name in current]
        return ToolPolicyPipelineResult(tool_names=ordered, trace=trace)

    def _expand_patterns(
        self,
        patterns: list[str],
        _alias_stack: Optional[set[str]] = None,
    ) -> set[str]:
        expanded: set[str] = set()
        alias_stack = set(_alias_stack or set())
        for pattern in patterns:
            token = str(pattern or "").strip()
            if not token:
                continue

            if token.startswith("group:"):
                for member in self._group_map.get(token, []):
                    if member in self._universe:
                        expanded.add(member)
                continue

            if token in self._aliases:
                if token in alias_stack:
                    continue
                expanded.update(
                    self._expand_patterns(
                        self._aliases[token],
                        _alias_stack=alias_stack | {token},
                    )
                )
                continue

            if any(ch in token for ch in ["*", "?", "["]):
                for name in self._universe:
                    if fnmatch(name, token):
                        expanded.add(name)
                continue

            if token in self._universe:
                expanded.add(token)
        return expanded


def build_ordered_policy_layers(
    *,
    policy: Optional[dict[str, Any]],
    provider_type: str = "",
    agent_id: str = "",
    channel: str = "",
    session_key: str = "",
) -> list[ToolPolicyLayer]:
    """Build fixed-order policy layers from runtime policy payload."""
    source = policy if isinstance(policy, dict) else {}

    layers = [
        ToolPolicyLayer("base_profile", **_read_rule(source.get("profile"))),
        ToolPolicyLayer("global", **_read_rule(source.get("global"))),
        ToolPolicyLayer(
            "provider",
            **_read_rule(_read_map_rule(source.get("by_provider"), provider_type)),
        ),
        ToolPolicyLayer(
            "agent",
            **_read_rule(_read_map_rule(source.get("by_agent"), agent_id)),
        ),
        ToolPolicyLayer(
            "channel_session",
            **_read_rule(
                _read_rule(source.get("channel"))
                if not channel
                else _read_map_rule(source.get("channel"), channel)
            ),
        ),
    ]

    if session_key:
        session_rule = _read_map_rule(source.get("by_session"), session_key)
        if session_rule:
            layers.append(ToolPolicyLayer("session", **_read_rule(session_rule)))
    return layers


def _read_map_rule(value: Any, key: str) -> dict[str, Any]:
    if not key or not isinstance(value, dict):
        return {}
    rule = value.get(key)
    return rule if isinstance(rule, dict) else {}


def _read_rule(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {"allow": [], "deny": []}
    allow = _normalize_rule_items(value.get("allow"))
    deny = _normalize_rule_items(value.get("deny"))
    return {"allow": allow, "deny": deny}


def _normalize_rule_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
