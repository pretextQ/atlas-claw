# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from typing import Any, Optional


def normalize_allowed_tool_names(value: Any) -> Optional[list[str]]:
    """Normalize a per-run allowed-tool payload.

    Returns ``None`` when no override should be applied. Returns a concrete list
    when the runtime should constrain tools, including ``[]`` to disable all
    direct function tools for the run.
    """
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names
    return []


def resolve_override_tools(
    *,
    agent: Any,
    allowed_tool_names: Optional[list[str]],
) -> Optional[list[Any]]:
    """Resolve direct function-tool objects for a per-run override."""
    normalized_names = normalize_allowed_tool_names(allowed_tool_names)
    if normalized_names is None:
        return None

    function_toolset = getattr(agent, "_function_toolset", None)
    tool_map = getattr(function_toolset, "tools", None)
    if not isinstance(tool_map, dict):
        return []

    if not normalized_names:
        return []

    resolved: list[Any] = []
    for tool_name in normalized_names:
        tool = tool_map.get(tool_name)
        if tool is None:
            continue
        resolved.append(tool)
    return resolved
