# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Selection helpers for Markdown-skill and provider-instance targets.

Extracted from runner_execution_prepare.py so each module owns one
responsibility (see docs/python-god-file-refactor-plan.md). The original
module re-exports every name below, so existing imports keep working.
"""

# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
from datetime import datetime
import logging
from pathlib import Path
import re
import time
from typing import Any, AsyncIterator, Optional

from app.atlasclaw.agent.prompt_builder import PromptMode
from app.atlasclaw.agent.context_pruning import prune_context_messages, should_apply_context_pruning
from app.atlasclaw.agent.context_window_guard import evaluate_context_window_guard
from app.atlasclaw.agent.runner_prompt_context import (
    build_system_prompt,
    collect_capability_index_snapshot,
    collect_tool_groups_snapshot,
    collect_tools_snapshot,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import (
    resolve_artifact_goal_from_intent_plan,
    selected_capability_ids_from_intent_plan,
)
from app.atlasclaw.agent.selected_capability import (
    SELECTED_CAPABILITY_KEY,
    get_selected_capability_from_deps,
    selected_capability_provider_instance_ref,
    selected_capability_targets,
    unique_capability_values,
)
from app.atlasclaw.agent.runner_tool.runner_tool_result_mode import normalize_tool_result_mode
from app.atlasclaw.agent.runner_tool.runner_tool_projection import (
    project_minimal_toolset,
    tool_is_coordination_support,
    turn_action_requires_tool_execution,
)
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import (
    CapabilitySelectorOutcome,
    ToolGateDecision,
    ToolIntentAction,
    ToolIntentPlan,
    ToolPolicyMode,
)
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.provider_skill_capability import provider_skill_capability_name
from app.atlasclaw.memory.access import MEMORY_TOOL_NAMES
from app.atlasclaw.tools.providers.instance_tools import (
    PROVIDER_INSTANCE_SELECTIONS_KEY,
    persist_provider_instance_selection,
)


logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()





def _intent_plan_has_explicit_targets(intent_plan: ToolIntentPlan | None) -> bool:
    if intent_plan is None:
        return False
    return any(
        [
            list(intent_plan.target_provider_instances or []),
            list(intent_plan.target_provider_types or []),
            list(intent_plan.target_provider_skill_names or []),
            list(intent_plan.target_skill_names or []),
            list(intent_plan.target_group_ids or []),
            list(intent_plan.target_capability_classes or []),
            list(intent_plan.target_tool_names or []),
        ]
    )


def _split_provider_instance_ref(value: Any) -> tuple[str, str]:
    normalized = _normalize_text(value)
    if "." not in normalized:
        return "", ""
    provider_type, instance_name = normalized.split(".", 1)
    return provider_type.strip(), instance_name.strip()


def _provider_instance_bucket(
    provider_instances: dict[str, Any],
    provider_type: str,
) -> tuple[str, dict[str, dict[str, Any]]]:
    normalized = _normalize_text(provider_type)
    if not normalized:
        return "", {}
    for key in (normalized, normalized.lower()):
        bucket = provider_instances.get(key)
        if isinstance(bucket, dict):
            return str(key), {
                str(name): dict(config)
                for name, config in bucket.items()
                if str(name or "").strip() and isinstance(config, dict)
            }
    for key, bucket in provider_instances.items():
        if str(key or "").strip().lower() != normalized.lower() or not isinstance(bucket, dict):
            continue
        return str(key), {
            str(name): dict(config)
            for name, config in bucket.items()
            if str(name or "").strip() and isinstance(config, dict)
        }
    return normalized, {}


def _apply_direct_provider_instance(
    *,
    extra: dict[str, Any],
    provider_type: str,
    instance_name: str,
    instance_config: dict[str, Any],
) -> None:
    instance = dict(instance_config)
    instance["provider_type"] = provider_type
    instance["instance_name"] = instance_name
    extra["provider_type"] = provider_type
    extra["provider_instance_name"] = instance_name
    extra["provider_instance"] = instance


def apply_provider_instance_selection_policy(
    *,
    deps: SkillDeps,
    intent_plan: ToolIntentPlan | None,
) -> tuple[ToolIntentPlan | None, dict[str, Any]]:
    """Apply instance-level routing targets to request extras before tool projection."""
    trace: dict[str, Any] = {
        "enabled": False,
        "selected_provider_instances": [],
        "target_provider_types": [],
    }
    if intent_plan is None or not isinstance(getattr(deps, "extra", None), dict):
        return intent_plan, trace

    extra = deps.extra
    provider_instances = extra.get("provider_instances")
    if not isinstance(provider_instances, dict) or not provider_instances:
        return intent_plan, trace

    target_refs: list[tuple[str, str, str, dict[str, Any]]] = []
    provider_types = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_provider_types or [])
        if _normalize_text(item)
    ]

    for raw_ref in intent_plan.target_provider_instances or []:
        raw_provider_type, instance_name = _split_provider_instance_ref(raw_ref)
        provider_type, bucket = _provider_instance_bucket(provider_instances, raw_provider_type)
        if not provider_type or not instance_name or instance_name not in bucket:
            continue
        target_refs.append(
            (
                provider_type,
                instance_name,
                f"{provider_type}.{instance_name}",
                bucket[instance_name],
            )
        )
        if provider_type.lower() not in provider_types:
            provider_types.append(provider_type.lower())

    if not target_refs:
        return intent_plan, trace

    selections = extra.setdefault(PROVIDER_INSTANCE_SELECTIONS_KEY, {})
    if not isinstance(selections, dict):
        selections = {}
        extra[PROVIDER_INSTANCE_SELECTIONS_KEY] = selections
    for provider_type, instance_name, _, instance_config in target_refs:
        selections[provider_type] = instance_name
        selections[provider_type.lower()] = instance_name
        if len(target_refs) == 1:
            _apply_direct_provider_instance(
                extra=extra,
                provider_type=provider_type,
                instance_name=instance_name,
                instance_config=instance_config,
            )

    selected_refs = [ref for _, _, ref, _ in target_refs]
    updated_plan = intent_plan.model_copy(
        update={
            "target_provider_instances": selected_refs,
            "target_provider_types": provider_types,
        }
    )
    trace.update(
        {
            "enabled": True,
            "selected_provider_instances": selected_refs,
            "target_provider_types": provider_types,
        }
    )
    return updated_plan, trace


async def persist_provider_instance_targets_from_intent_plan(
    *,
    deps: SkillDeps,
    intent_plan: ToolIntentPlan | None,
) -> list[str]:
    """Persist provider instances fixed by a provider-skill routing plan."""
    if intent_plan is None:
        return []

    persisted_refs: list[str] = []
    for raw_ref in intent_plan.target_provider_instances or []:
        provider_type, instance_name = _split_provider_instance_ref(raw_ref)
        if not provider_type or not instance_name:
            continue
        await persist_provider_instance_selection(deps, provider_type, instance_name)
        persisted_refs.append(f"{provider_type}.{instance_name}")
    return persisted_refs


def _active_provider_instance_names_from_extra(extra: dict[str, Any]) -> list[str]:
    """Return provider instance names already fixed in the current session/run scope."""
    names: list[str] = []
    direct_instance_name = _normalize_text(extra.get("provider_instance_name"))
    if direct_instance_name:
        names.append(direct_instance_name)

    selections = extra.get(PROVIDER_INSTANCE_SELECTIONS_KEY)
    if isinstance(selections, dict):
        for instance_name in selections.values():
            normalized_instance_name = _normalize_text(instance_name)
            if normalized_instance_name:
                names.append(normalized_instance_name)
    return unique_capability_values(names)


def _artifact_classes_for_entry(entry: dict[str, Any]) -> set[str]:
    return {
        f"artifact:{str(item).strip().lower()}"
        for item in (entry.get("artifact_types", []) or [])
        if str(item).strip()
    }


