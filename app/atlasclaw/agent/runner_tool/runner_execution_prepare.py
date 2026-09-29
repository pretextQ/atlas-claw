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


def _is_memory_tool(tool: dict[str, Any]) -> bool:
    """Return whether a runtime tool is one of the read-only memory tools."""
    name = str(tool.get("name", "") or "").strip()
    capability_class = str(tool.get("capability_class", "") or "").strip().lower()
    group_ids = {
        str(group_id or "").strip().lower()
        for group_id in (tool.get("group_ids", []) or [])
        if str(group_id or "").strip()
    }
    return (
        name in MEMORY_TOOL_NAMES
        or capability_class == "memory"
        or "group:memory" in group_ids
    )


def filter_implicit_memory_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Hide read-only memory tools from ordinary natural-language routing.

    Users can still select memory tools explicitly via slash capability. For
    plain chat turns, memory recall and writes are handled by the active-memory
    and auto-write services so memory cannot steer skill/tool selection or
    surface unrelated search results.
    """
    filtered: list[dict[str, Any]] = []
    removed: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", "") or "").strip()
        if _is_memory_tool(tool):
            if name:
                removed.append(name)
            continue
        filtered.append(tool)
    return filtered, removed


def select_execution_prompt_mode(
    *,
    intent_action: str,
    is_follow_up: bool,
    projected_tool_count: int,
    has_target_md_skill: bool = False,
) -> PromptMode:
    """Choose a lighter prompt for explicit tool turns with a small projected toolset."""
    normalized_action = str(intent_action or "").strip().lower()
    safe_projected_count = max(0, int(projected_tool_count or 0))
    if has_target_md_skill:
        return PromptMode.MINIMAL
    if not normalized_action and safe_projected_count == 0:
        return PromptMode.MINIMAL
    if (
        normalized_action in {
            ToolIntentAction.DIRECT_ANSWER.value,
            ToolIntentAction.ASK_CLARIFICATION.value,
        }
        and safe_projected_count == 0
    ):
        return PromptMode.MINIMAL
    if normalized_action != ToolIntentAction.USE_TOOLS.value:
        return PromptMode.FULL
    if is_follow_up:
        return PromptMode.FULL
    if 0 < safe_projected_count <= 12:
        return PromptMode.MINIMAL
    return PromptMode.FULL


def should_resolve_target_md_skill(intent_plan: ToolIntentPlan | None) -> bool:
    """Load the target markdown skill whenever the turn has an explicit md-skill target."""
    if intent_plan is None:
        return False
    if any(str(item).strip() for item in (intent_plan.target_skill_names or [])):
        return True
    if any(str(item).strip() for item in (intent_plan.target_provider_skill_names or [])):
        return True
    if any(str(item).strip() for item in (intent_plan.target_tool_names or [])):
        return True
    return turn_action_requires_tool_execution(intent_plan)


def build_user_selected_tool_intent_plan(deps: SkillDeps) -> ToolIntentPlan | None:
    """Translate a validated slash capability into a runtime tool plan."""
    selected = get_selected_capability_from_deps(deps)
    if not selected:
        return None
    extra = getattr(deps, "extra", None)
    request_context = extra.get("context") if isinstance(extra, dict) else None
    if isinstance(request_context, dict) and isinstance(
        request_context.get("embed_scope"), dict
    ):
        return None

    targets = selected_capability_targets(selected)
    if not targets.has_any():
        return None

    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=targets.provider_instances,
        target_provider_types=targets.provider_types,
        target_provider_skill_names=targets.provider_skill_names,
        target_capability_classes=targets.capability_classes,
        target_skill_names=targets.skill_names,
        target_group_ids=targets.group_ids,
        target_tool_names=targets.tool_names,
        reason="user_selected_capability",
    )


def _selected_plan_matches_active_capability(
    *,
    intent_plan: ToolIntentPlan | None,
    active_provider_skill: Optional[str],
    active_skill: Optional[str],
) -> bool:
    """Return whether a repeated selected capability is the current workflow scope."""
    if intent_plan is None:
        return False
    normalized_provider_skill = _normalize_text(active_provider_skill).lower()
    if normalized_provider_skill:
        return normalized_provider_skill in {
            _normalize_text(item).lower()
            for item in (intent_plan.target_provider_skill_names or [])
            if _normalize_text(item)
        }
    normalized_skill = _normalize_text(active_skill).lower()
    if normalized_skill:
        return normalized_skill in {
            _normalize_text(item).lower()
            for item in (intent_plan.target_skill_names or [])
            if _normalize_text(item)
        }
    return False


def build_preselected_md_skill_intent_plan(deps: SkillDeps) -> ToolIntentPlan | None:
    """Translate a request-scoped target markdown skill into a hard skill plan."""
    extra = getattr(deps, "extra", None)
    if not isinstance(extra, dict):
        return None

    target_md_skill = extra.get("target_md_skill")
    if not isinstance(target_md_skill, dict):
        return None

    # Webhook dispatch stores a validated provider-qualified skill here. Require
    # the canonical name so this hard plan has no legacy or display-name fallback.
    qualified_name = _normalize_text(target_md_skill.get("qualified_name"))
    if not qualified_name:
        return None

    provider_type = _normalize_text(
        target_md_skill.get("provider") or target_md_skill.get("provider_type")
    )
    target_provider_instances = unique_capability_values(
        target_md_skill.get("target_provider_instances")
    )
    target_provider_types = unique_capability_values(target_md_skill.get("target_provider_types"))
    target_provider_skill_names = unique_capability_values(
        target_md_skill.get("target_provider_skill_names")
    )
    if provider_type and (
        not target_provider_instances
        or not target_provider_types
        or not target_provider_skill_names
    ):
        return None
    return ToolIntentPlan(
        action=ToolIntentAction.USE_TOOLS,
        target_provider_instances=target_provider_instances,
        target_provider_types=target_provider_types,
        target_provider_skill_names=target_provider_skill_names,
        target_skill_names=[] if provider_type else [qualified_name],
        target_group_ids=[f"group:{provider_type}"] if provider_type else [],
        reason="preselected_target_md_skill",
    )


def select_explicit_tool_execution_target(
    *,
    intent_plan: ToolIntentPlan | None,
    is_follow_up: bool,
    projected_tools: list[dict[str, Any]],
    has_target_md_skill: bool = False,
) -> Optional[dict[str, Any]]:
    """Return the single direct-execution tool for low-noise explicit tool turns."""
    actionable_turn = (
        intent_plan is not None
        and intent_plan.action in {ToolIntentAction.USE_TOOLS, ToolIntentAction.CREATE_ARTIFACT}
    )
    if not actionable_turn:
        return None

    candidate_tools: list[dict[str, Any]] = []
    for tool in projected_tools or []:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("name", "") or "").strip()
        if not tool_name or tool_is_coordination_support(tool):
            continue
        candidate_tools.append(tool)

    if len(candidate_tools) != 1:
        return None

    target_tool = candidate_tools[0]
    normalized_result_mode = normalize_tool_result_mode(target_tool)
    if normalized_result_mode == "silent_ok":
        # When the runtime has already narrowed execution to exactly one silent tool,
        # prefer the compact single-tool prompt so the model performs the tool call
        # instead of drifting into extra narration.
        return dict(target_tool)
    if is_follow_up:
        return None
    if has_target_md_skill:
        return None
    if normalized_result_mode != "tool_only_ok":
        return None
    return dict(target_tool)


def toolset_has_only_coordination_support_tools(tools: list[dict[str, Any]]) -> bool:
    """Return whether the projected set contains only non-executing support tools."""
    normalized_tools = [
        tool
        for tool in (tools or [])
        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
    ]
    if not normalized_tools:
        return False
    for tool in normalized_tools:
        group = str(tool.get("group", "") or "").strip()
        capability_class = str(tool.get("capability_class", "") or "").strip()
        if group == "skill_runtime" or capability_class.startswith("skill_runtime:"):
            return False
    return all(tool_is_coordination_support(tool) for tool in normalized_tools)


def build_explicit_tool_execution_prompt(
    *,
    tool: dict[str, Any],
    now_local: Optional[datetime] = None,
) -> str:
    """Build a tiny system prompt for single-tool explicit execution turns."""
    tool_name = str(tool.get("name", "") or "").strip() or "tool"
    description = str(tool.get("description", "") or "").strip() or "No description provided."
    capability_class = str(tool.get("capability_class", "") or "").strip()
    provider_type = str(tool.get("provider_type", "") or "").strip()
    result_mode = normalize_tool_result_mode(tool) or "llm"
    parameters_schema = tool.get("parameters_schema", {})
    required_fields: list[str] = []
    properties: dict[str, Any] = {}
    if isinstance(parameters_schema, dict):
        raw_properties = parameters_schema.get("properties")
        if isinstance(raw_properties, dict):
            properties = raw_properties
        required_fields = [
            str(item).strip()
            for item in (parameters_schema.get("required", []) or [])
            if str(item).strip()
        ]

    local_now = (now_local or datetime.now().astimezone()).isoformat(timespec="seconds")
    argument_lines: list[str] = []
    for field_name, field_spec in properties.items():
        if not isinstance(field_spec, dict):
            continue
        type_name = str(field_spec.get("type", "") or "string").strip()
        field_desc = str(field_spec.get("description", "") or "").strip()
        required_label = "required" if field_name in required_fields else "optional"
        line = f"- {field_name} ({type_name}, {required_label})"
        if field_desc:
            line += f": {field_desc}"
        argument_lines.append(line)
    if not argument_lines:
        argument_lines.append("- no explicit arguments")

    capability_line = capability_class or "unknown"
    if provider_type:
        capability_line = f"{capability_line}; provider={provider_type}"

    prompt = (
        "You are the assistant.\n"
        "This turn has already been narrowed to exactly one allowed runtime tool.\n"
        "Your valid actions are:\n"
        "1) If the tool has not been called yet this turn, call the allowed tool exactly once with concrete arguments.\n"
        "2) If the tool result is already available in the conversation, use that evidence to continue the workflow.\n"
        "3) Ask one concise clarification question only if required inputs are still missing.\n"
        "Do not answer from memory.\n"
        "Do not mention hidden reasoning.\n"
        "Do not mention any other tool.\n\n"
        f"Current local time:\n{local_now}\n\n"
        "Allowed tool:\n"
        f"- name: {tool_name}\n"
        f"- description: {description}\n"
        f"- capability: {capability_line}\n"
        f"- result_mode: {result_mode}\n"
        "Arguments:\n"
        f"{chr(10).join(argument_lines)}\n"
    )
    if result_mode == "silent_ok":
        prompt += (
            "If you call this tool, continue directly to the next user-facing question or confirmation afterward.\n"
            "If this tool is an internal lookup step, do not stop at the lookup result.\n"
            "Phrase that next user-facing step naturally instead of narrating the lookup itself.\n"
            "Use only the resolved facts from the lookup. Never quote scaffolding phrases such as "
            "'Found N ...', replay numbered raw dumps, or surface raw JSON or unlabeled UUID/ID dumps.\n"
            "Do not call the same tool again with the same arguments after its result is available.\n"
            "Do not mention the tool call to the user and do not surface its raw output.\n"
        )
    return prompt




def _build_md_skill_tool_index(
    *,
    md_skills_snapshot: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Build a qualified skill -> declared tool names index."""
    skill_tool_index: dict[str, set[str]] = {}
    for skill in md_skills_snapshot:
        if not isinstance(skill, dict):
            continue
        qname = str(
            skill.get("qualified_name") or skill.get("name") or ""
        ).strip().lower()
        if not qname:
            continue
        metadata = skill.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        declared: set[str] = set()
        for key, value in metadata.items():
            key_str = str(key)
            if key_str.startswith("tool_") and key_str.endswith("_name"):
                tool_name = str(value or "").strip().lower()
                if tool_name:
                    declared.add(tool_name)
        single = str(metadata.get("tool_name", "")).strip().lower()
        if single:
            declared.add(single)
        for raw_list_key in ("declared_tool_names", "tool_names"):
            for item in (metadata.get(raw_list_key) or skill.get(raw_list_key) or []):
                tool_name = str(item).strip().lower()
                if tool_name:
                    declared.add(tool_name)
        if declared:
            skill_tool_index[qname] = declared
    return skill_tool_index


def _md_skill_snapshot_has_provider_binding(
    skill: dict[str, Any],
    *,
    provider_instances: Any = None,
) -> bool:
    """Return whether a markdown skill must be selected through provider_name.skill."""
    if not isinstance(skill, dict):
        return False
    metadata = skill.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    explicit_provider_type = _normalize_text(
        metadata.get("provider_type")
        or skill.get("provider_type")
        or skill.get("provider")
    )
    if explicit_provider_type:
        return True

    qualified_name = _normalize_text(skill.get("qualified_name") or skill.get("name"))
    if ":" not in qualified_name:
        return False
    provider_type = qualified_name.split(":", 1)[0].strip()
    _, bucket = _provider_instance_bucket(provider_instances or {}, provider_type)
    return bool(bucket)


def _recent_tool_names_from_transcript(
    *,
    message_history: list[dict[str, Any]],
    max_scan: int,
) -> list[str]:
    """Collect recent tool names from transcript evidence, newest first."""
    recent_tool_names: list[str] = []

    def _append_tool_name(raw_name: Any) -> None:
        tool_name = str(raw_name or "").strip().lower()
        if tool_name and tool_name not in recent_tool_names:
            recent_tool_names.append(tool_name)

    for msg in reversed(message_history[-max_scan:]):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "").strip().lower()
        if role in {"tool", "toolresult", "tool_result"}:
            _append_tool_name(msg.get("tool_name", "") or msg.get("name", ""))
        for tool_result in reversed(msg.get("tool_results", []) or []):
            if not isinstance(tool_result, dict):
                continue
            _append_tool_name(tool_result.get("tool_name", "") or tool_result.get("name", ""))
        for tool_call in reversed(msg.get("tool_calls", []) or []):
            if not isinstance(tool_call, dict):
                continue
            _append_tool_name(tool_call.get("name", "") or tool_call.get("tool_name", ""))
        for summary in reversed(msg.get("tool_call_summaries", []) or []):
            if not isinstance(summary, dict):
                continue
            _append_tool_name(summary.get("name", "") or summary.get("tool_name", ""))
    return recent_tool_names


def _infer_active_skill_from_transcript(
    *,
    message_history: list[dict[str, Any]],
    md_skills_snapshot: list[dict[str, Any]],
    provider_instances: Any = None,
    max_scan: int = 20,
) -> Optional[str]:
    """Scan recent transcript tool calls to infer the currently active md skill.

    Returns the qualified name of the Markdown skill whose declared tools
    appear most recently in the conversation. The capability selector receives
    this as a non-binding active-workflow candidate; the model still decides
    whether to continue it, switch capabilities, or use ordinary conversation.
    """
    if not message_history or not md_skills_snapshot:
        return None

    recent_tool_names = _recent_tool_names_from_transcript(
        message_history=message_history,
        max_scan=max_scan,
    )

    if not recent_tool_names:
        return None

    standalone_md_skills = [
        skill
        for skill in md_skills_snapshot
        if not _md_skill_snapshot_has_provider_binding(
            skill,
            provider_instances=provider_instances,
        )
    ]
    skill_tool_index = _build_md_skill_tool_index(md_skills_snapshot=standalone_md_skills)
    if not skill_tool_index:
        return None

    # Pick the skill whose declared tools match the most-recent transcript tool
    for tool_name in recent_tool_names:
        for qname, declared_tools in skill_tool_index.items():
            if tool_name in declared_tools:
                return qname

    return None


def _infer_active_provider_skill_from_transcript(
    *,
    message_history: list[dict[str, Any]],
    capability_index: list[dict[str, Any]],
    active_provider_name: str = "",
    active_provider_names: Optional[list[str]] = None,
    max_scan: int = 20,
) -> Optional[str]:
    """Infer the active provider-name.skill target from recent provider tool calls."""
    provider_entries = [
        entry
        for entry in capability_index
        if isinstance(entry, dict)
        and _normalize_text(entry.get("kind", "")).lower() == "provider_skill"
    ]
    if not message_history or not provider_entries:
        return None

    recent_tool_names = _recent_tool_names_from_transcript(
        message_history=message_history,
        max_scan=max_scan,
    )
    if not recent_tool_names:
        return None

    skill_tool_index = _build_md_skill_tool_index(md_skills_snapshot=provider_entries)
    if not skill_tool_index:
        return None

    provider_prefixes = []
    for raw_name in [active_provider_name, *(active_provider_names or [])]:
        provider_name = _normalize_text(raw_name).lower()
        if provider_name and provider_name not in provider_prefixes:
            provider_prefixes.append(provider_name)
    for tool_name in recent_tool_names:
        matches = [
            skill_name
            for skill_name, declared_tools in skill_tool_index.items()
            if tool_name in declared_tools
        ]
        if not matches:
            continue
        for provider_prefix in provider_prefixes:
            for skill_name in matches:
                if skill_name.startswith(f"{provider_prefix}."):
                    return skill_name
        if len(matches) == 1:
            return matches[0]
    return None


# Re-exported from split modules so existing imports keep working unchanged.
from app.atlasclaw.agent.runner_tool.runner_prepare_md_skill import _normalize_text  # noqa: F401
from app.atlasclaw.agent.runner_tool.runner_prepare_md_skill import (  # noqa: F401
    _intent_plan_has_explicit_targets,
    _split_provider_instance_ref,
    _provider_instance_bucket,
    _apply_direct_provider_instance,
    apply_provider_instance_selection_policy,
    persist_provider_instance_targets_from_intent_plan,
    _active_provider_instance_names_from_extra,
    _artifact_classes_for_entry,
)
from app.atlasclaw.agent.runner_tool.runner_prepare_workflow_context import (  # noqa: F401
    _match_selected_md_skill_entry,
    _rank_selected_md_skill_entry,
    _load_target_md_skill_full_instructions,
    resolve_selected_md_skill_target,
    enrich_target_md_skill_with_workflow_context,
    _normalize_embed_scope_identity,
    _embed_scope_workflow_history,
    _parse_target_md_skill_workflow_metadata,
    _infer_active_request_trace_id,
    _extract_trace_id_from_metadata,
    _extract_workflow_candidate_items_from_metadata,
    _workflow_candidate_selection_tokens,
    _normalize_workflow_candidate_text,
    _workflow_candidate_mention_tokens,
    _collect_explicit_selection_tokens,
    _narrow_target_md_skill_workflow_metadata,
    _collect_same_flow_following_messages,
    build_target_md_skill_workflow_context,
    prune_auto_selected_provider_instance_tools,
)



def _target_md_skill_declares_tools(
    *,
    target_md_skill: dict[str, Any],
    md_skills_snapshot: list[dict[str, Any]],
) -> bool:
    """Return whether the selected markdown skill already registers executable tools."""
    qname = _normalize_text(target_md_skill.get("qualified_name")).lower()
    if not qname:
        return False
    for skill in md_skills_snapshot or []:
        if not isinstance(skill, dict):
            continue
        skill_qname = _normalize_text(
            skill.get("qualified_name") or skill.get("name")
        ).lower()
        if skill_qname != qname:
            continue
        metadata = skill.get("metadata")
        if not isinstance(metadata, dict):
            return False
        if _normalize_text(metadata.get("tool_name")) and _normalize_text(
            metadata.get("entrypoint")
        ):
            return True
        ids: set[str] = set()
        for key in metadata.keys():
            key_text = str(key or "")
            if key_text.startswith("tool_") and key_text.endswith("_name"):
                ids.add(key_text[len("tool_") : -len("_name")])
            elif key_text.startswith("tool_") and key_text.endswith("_entrypoint"):
                ids.add(key_text[len("tool_") : -len("_entrypoint")])
        for tool_id in ids:
            if _normalize_text(metadata.get(f"tool_{tool_id}_name")) and _normalize_text(
                metadata.get(f"tool_{tool_id}_entrypoint")
            ):
                return True
        return False
    return False


def _target_md_skill_is_provider_bound(
    *,
    target_md_skill: dict[str, Any],
    md_skills_snapshot: list[dict[str, Any]],
) -> bool:
    """Return whether the selected markdown skill depends on a provider instance."""
    provider = _normalize_text(target_md_skill.get("provider"))
    if provider:
        return True
    qname = _normalize_text(target_md_skill.get("qualified_name")).lower()
    if not qname:
        return False
    for skill in md_skills_snapshot or []:
        if not isinstance(skill, dict):
            continue
        skill_qname = _normalize_text(
            skill.get("qualified_name") or skill.get("name")
        ).lower()
        if skill_qname != qname:
            continue
        metadata = skill.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return bool(
            _normalize_text(metadata.get("provider_type"))
            or _normalize_text(skill.get("provider"))
        )
    return False


def inject_standard_skill_runtime_tools(
    *,
    available_tools: list[dict[str, Any]],
    deps: SkillDeps,
    target_md_skill: dict[str, Any] | None,
    execution_allowed: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Append internal runtime helpers for docs-only standard markdown skills.

    Provider-bound skills and skills that declare AtlasClaw executable metadata
    use their own registered tools. The internal runtime is reserved for
    selected standard markdown skills that otherwise only provide instructions.
    When execution is not authorized, instructions remain available but no
    internal runtime helper is exposed.
    """
    trace: dict[str, Any] = {
        "enabled": False,
        "reason": "no_target_md_skill",
        "tool_names": [],
    }
    if not execution_allowed:
        trace["reason"] = "execution_not_authorized"
        if isinstance(getattr(deps, "extra", None), dict):
            deps.extra.pop("standard_skill_runtime_enabled", None)
            deps.extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill
    if not isinstance(target_md_skill, dict):
        if isinstance(getattr(deps, "extra", None), dict):
            deps.extra.pop("standard_skill_runtime_enabled", None)
            deps.extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    extra = deps.extra if isinstance(getattr(deps, "extra", None), dict) else {}
    md_skills_snapshot = extra.get("md_skills_snapshot")
    if not isinstance(md_skills_snapshot, list):
        md_skills_snapshot = []
    if _target_md_skill_is_provider_bound(
        target_md_skill=target_md_skill,
        md_skills_snapshot=md_skills_snapshot,
    ):
        trace["reason"] = "target_skill_provider_bound"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill
    if _target_md_skill_declares_tools(
        target_md_skill=target_md_skill,
        md_skills_snapshot=md_skills_snapshot,
    ):
        trace["reason"] = "target_skill_has_executable_tool"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    runtime_tools = extra.get("internal_runtime_tools_snapshot")
    if not isinstance(runtime_tools, list) or not runtime_tools:
        trace["reason"] = "internal_runtime_tools_unavailable"
        extra.pop("standard_skill_runtime_enabled", None)
        extra.pop("standard_skill_runtime_tools_visible", None)
        return list(available_tools or []), trace, target_md_skill

    existing = {
        str(tool.get("name", "") or "").strip()
        for tool in (available_tools or [])
        if isinstance(tool, dict)
    }
    result = list(available_tools or [])
    added: list[str] = []
    for tool in runtime_tools:
        if not isinstance(tool, dict):
            continue
        tool_name = _normalize_text(tool.get("name"))
        if not tool_name or tool_name in existing:
            continue
        result.append(dict(tool))
        existing.add(tool_name)
        added.append(tool_name)

    if not added:
        trace["reason"] = "internal_runtime_tools_already_present"
        return result, trace, target_md_skill

    enriched_target = dict(target_md_skill)
    enriched_target["standard_runtime_enabled"] = True
    enriched_target["standard_runtime_tool_names"] = list(added)
    extra["standard_skill_runtime_enabled"] = True
    extra["standard_skill_runtime_tools_visible"] = True
    extra["target_md_skill"] = dict(enriched_target)
    trace.update(
        {
            "enabled": True,
            "reason": "target_standard_skill_selected",
            "tool_names": list(added),
        }
    )
    return result, trace, enriched_target




class RunnerExecutionPreparePhaseMixin:
    """Prepare routing, prompt context, and projected tools before model execution."""

    async def _run_prepare_phase(self, *, state: dict[str, Any], _log_step: Any) -> AsyncIterator[StreamEvent]:
        """Prepare runtime/session/prompt/tool-gate phase before model loop."""
        session_key = state.get("session_key")
        user_message = state.get("user_message")
        deps = state.get("deps")
        max_tool_calls = state.get("max_tool_calls")
        timeout_seconds = state.get("timeout_seconds")
        _token_failover_attempt = state.get("_token_failover_attempt")
        _emit_lifecycle_bounds = state.get("_emit_lifecycle_bounds")
        start_time = state.get("start_time")
        tool_calls_count = state.get("tool_calls_count")
        compaction_applied = state.get("compaction_applied")
        thinking_emitter = state.get("thinking_emitter")
        persist_override_messages = state.get("persist_override_messages")
        persist_override_base_len = state.get("persist_override_base_len")
        runtime_agent = state.get("runtime_agent")
        selected_token_id = state.get("selected_token_id")
        release_slot = state.get("release_slot")
        extra = state.get("extra")
        run_id = state.get("run_id")
        tool_execution_retry_count = state.get("tool_execution_retry_count")
        run_failed = state.get("run_failed")
        message_history = state.get("message_history")
        system_prompt = state.get("system_prompt")
        final_assistant = state.get("final_assistant")
        context_history_for_hooks = state.get("context_history_for_hooks")
        tool_call_summaries = state.get("tool_call_summaries")
        session_title = state.get("session_title")
        buffered_assistant_events = state.get("buffered_assistant_events")
        assistant_output_streamed = state.get("assistant_output_streamed")
        tool_request_message = state.get("tool_request_message")
        # Initialize every local the finally block publishes: the early-exit
        # paths (context guard, empty toolset) reach the finally before these
        # are assigned, and an unbound local there raised NameError and masked
        # the real failure.
        model_user_message = state.get("model_user_message") or user_message
        tool_intent_plan = state.get("tool_intent_plan")
        tool_gate_decision = state.get("tool_gate_decision")
        tool_match_result = state.get("tool_match_result")
        current_model_attempt = state.get("current_model_attempt")
        current_attempt_started_at = state.get("current_attempt_started_at")
        current_attempt_has_text = state.get("current_attempt_has_text")
        current_attempt_has_tool = state.get("current_attempt_has_tool")
        reasoning_retry_count = state.get("reasoning_retry_count")
        run_output_start_index = state.get("run_output_start_index")
        tool_execution_required = state.get("tool_execution_required")
        reasoning_retry_limit = state.get("reasoning_retry_limit")
        model_stream_timed_out = state.get("model_stream_timed_out")
        model_timeout_error_message = state.get("model_timeout_error_message")
        runtime_context_window_info = state.get("runtime_context_window_info")
        runtime_context_guard = state.get("runtime_context_guard")
        runtime_context_window = state.get("runtime_context_window")
        session_manager = state.get("session_manager")
        session = state.get("session")
        transcript = state.get("transcript")
        all_available_tools = state.get("all_available_tools")
        tool_groups_snapshot = state.get("tool_groups_snapshot")
        available_tools = state.get("available_tools")
        toolset_filter_trace = state.get("toolset_filter_trace")
        tool_projection_trace = state.get("tool_projection_trace")
        used_toolset_fallback = state.get("used_toolset_fallback")
        metadata_candidates = state.get("metadata_candidates")
        ranking_trace = state.get("ranking_trace")
        artifact_goal = state.get("artifact_goal")
        runtime_message_history = state.get("runtime_message_history")
        session_message_history = state.get("session_message_history")
        runtime_base_history_len = state.get("runtime_base_history_len")
        persist_run_output_start_index = state.get("persist_run_output_start_index")
        prompt_mode = state.get("prompt_mode") or ""
        try:
            if _emit_lifecycle_bounds:
                yield StreamEvent.lifecycle_start()
            _log_step("lifecycle_start")
            yield StreamEvent.runtime_update(
                "reasoning",
                "Starting response analysis.",
                metadata={"phase": "start", "attempt": 0, "elapsed": 0.0},
            )

            runtime_agent, selected_token_id, release_slot = await self._resolve_runtime_agent(session_key, deps)
            # Publish the release handle immediately: a consumer that stops
            # iterating after a later error event must still be able to
            # release the permit from run()'s finally block.
            state["release_slot"] = release_slot
            logger.warning(
                "runtime token resolved: session=%s selected_token_id=%s managed_tokens=%s",
                session_key,
                selected_token_id,
                len(self.token_policy.token_pool.tokens) if self.token_policy is not None else 0,
            )
            runtime_context_window_info = self._resolve_runtime_context_window_info(selected_token_id, deps)
            runtime_context_guard = evaluate_context_window_guard(
                tokens=runtime_context_window_info.tokens,
                source=runtime_context_window_info.source,
            )
            runtime_context_window = runtime_context_guard.tokens
            _log_step(
                "context_guard_evaluated",
                tokens=runtime_context_guard.tokens,
                source=runtime_context_guard.source,
                should_warn=runtime_context_guard.should_warn,
                should_block=runtime_context_guard.should_block,
            )
            if runtime_context_guard.should_warn:
                yield StreamEvent.runtime_update(
                    "warning",
                    (
                        "Model context window is below the warning threshold. "
                        f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                    ),
                    metadata={
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "warn",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
            if runtime_context_guard.should_block:
                failure_message = (
                    "Model context window is below the minimum safety threshold. "
                    f"tokens={runtime_context_guard.tokens}, source={runtime_context_guard.source}"
                )
                run_failed = True
                await self.runtime_events.trigger_llm_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                await self.runtime_events.trigger_run_failed(
                    session_key=session_key,
                    run_id=run_id,
                    error=failure_message,
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={
                        "phase": "context_guard",
                        "tokens": runtime_context_guard.tokens,
                        "source": runtime_context_guard.source,
                        "guard": "block",
                        "elapsed": round(time.monotonic() - start_time, 1),
                    },
                )
                yield StreamEvent.error_event(failure_message)
                state["should_stop"] = True
                return
            session_manager = self._resolve_session_manager(session_key, deps)

            # --:session + build prompt --

            session = await session_manager.get_or_create(session_key)
            _log_step("session_get_or_create_done")
            transcript = await session_manager.load_transcript(session_key)
            _log_step("session_load_transcript_done", transcript_entries=len(transcript))
            message_history = self.history.build_message_history(transcript)
            message_history = self.history.prune_summary_messages(message_history)
            if should_apply_context_pruning(settings=self.context_pruning_settings, session=session):
                message_history = prune_context_messages(
                    messages=message_history,
                    settings=self.context_pruning_settings,
                    context_window_tokens=runtime_context_window,
                )
            message_history = self._deduplicate_message_history(message_history)
            context_history_for_hooks = list(message_history)
            session_title = str(getattr(session, "title", "") or "")
            await self.runtime_events.trigger_message_received(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_message_received_dispatched")
            await self.runtime_events.trigger_run_started(
                session_key=session_key,
                run_id=run_id,
                user_message=user_message,
            )
            _log_step("hook_run_started_dispatched")
            await self._maybe_set_draft_title(
                session_manager=session_manager,
                session_key=session_key,
                session=session,
                transcript=transcript,
                user_message=user_message,
            )
            _log_step("session_draft_title_done")
            all_available_tools = collect_tools_snapshot(agent=runtime_agent, deps=deps)
            # Apply skill permission filtering: remove handler tools whose skill
            # is disabled in the user's role.  Two filtering strategies:
            # 1. _disabled_tool_names: exact tool name match (from md_skill metadata)
            # 2. _disabled_skill_ids: match by skill_name/qualified_skill_name field
            _extra = deps.extra or {}
            _disabled_tools = _extra.get("_disabled_tool_names")
            _disabled_sids = _extra.get("_disabled_skill_ids")
            if (isinstance(_disabled_tools, set) and _disabled_tools) or (
                isinstance(_disabled_sids, set) and _disabled_sids
            ):
                _dt = _disabled_tools if isinstance(_disabled_tools, set) else set()
                _ds = _disabled_sids if isinstance(_disabled_sids, set) else set()
                def _tool_allowed(t: dict) -> bool:
                    tname = str(t.get("name", "") or "").strip()
                    if tname and tname in _dt:
                        return False
                    if _ds:
                        sname = str(t.get("skill_name", "") or "").strip().lower()
                        qsname = str(t.get("qualified_skill_name", "") or "").strip().lower()
                        bare_sname = sname.split(":")[-1] if sname else ""
                        bare_qsname = qsname.split(":")[-1] if qsname else ""
                        if (sname and (sname in _ds or bare_sname in _ds)) or (
                            qsname and (qsname in _ds or bare_qsname in _ds)
                        ):
                            return False
                    return True
                all_available_tools = [t for t in all_available_tools if _tool_allowed(t)]
            _log_step("tools_snapshot_collected", all_tools_count=len(all_available_tools))
            tool_groups_snapshot = collect_tool_groups_snapshot(deps)
            _log_step("tool_groups_snapshot_collected", group_count=len(tool_groups_snapshot))
            available_tools, toolset_filter_trace, used_toolset_fallback = self._build_turn_toolset(
                deps=deps,
                session_key=session_key,
                all_tools=all_available_tools,
                tool_groups=tool_groups_snapshot,
            )
            _log_step(
                "toolset_policy_applied",
                total_tools=len(all_available_tools),
                filtered_tools=len(available_tools),
                used_fallback=used_toolset_fallback,
                policy_layers=len(toolset_filter_trace),
            )
            if isinstance(deps.extra, dict):
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["toolset_policy_trace"] = list(toolset_filter_trace)
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            tool_request_message = " ".join(str(user_message or "").split()).strip()
            if not tool_request_message:
                tool_request_message = user_message
            used_follow_up_context = False
            model_user_message = user_message
            if isinstance(deps.extra, dict):
                deps.extra.pop("current_follow_up_context", None)
            target_md_skill_workflow_context = build_target_md_skill_workflow_context(
                recent_history=message_history,
            )
            transcript_active_skill = None
            transcript_active_provider_skill = None
            capability_index: list[dict[str, Any]] | None = collect_capability_index_snapshot(
                agent=runtime_agent or self.agent,
                deps=deps,
            )
            md_skills_snapshot = (
                list(deps.extra.get("md_skills_snapshot") or [])
                if isinstance(deps.extra, dict)
                else []
            )
            transcript_active_provider_skill = _infer_active_provider_skill_from_transcript(
                message_history=message_history,
                capability_index=capability_index,
                active_provider_name=(
                    str(deps.extra.get("provider_instance_name", "") or "")
                    if isinstance(deps.extra, dict)
                    else ""
                ),
                active_provider_names=(
                    _active_provider_instance_names_from_extra(deps.extra)
                    if isinstance(deps.extra, dict)
                    else []
                ),
            )
            if not transcript_active_provider_skill:
                transcript_active_skill = _infer_active_skill_from_transcript(
                    message_history=message_history,
                    md_skills_snapshot=md_skills_snapshot,
                    provider_instances=(
                        deps.extra.get("provider_instances")
                        if isinstance(deps.extra, dict)
                        else None
                    ),
                )
            selected_tool_intent_plan = build_user_selected_tool_intent_plan(deps)
            capability_selector_intent_plan: ToolIntentPlan | None = None
            capability_selector_failed = False
            server_page_skill_scope = False
            server_page_skill_refs: list[str] = []
            server_page_provider_type = ""
            server_page_provider_instance = ""
            server_page_action_start = False
            if isinstance(getattr(deps, "extra", None), dict):
                request_context = deps.extra.get("context")
                embed_scope = (
                    request_context.get("embed_scope")
                    if isinstance(request_context, dict)
                    else None
                )
                page_skill_refs = (
                    request_context.get("allowed_page_skill_refs")
                    if isinstance(request_context, dict)
                    else None
                )
                if isinstance(embed_scope, dict):
                    server_page_skill_scope = True
                    server_page_action_start = bool(
                        isinstance(request_context, dict)
                        and request_context.get("visible_user_turn") is False
                    )
                    server_page_skill_refs = unique_capability_values(
                        page_skill_refs if isinstance(page_skill_refs, list) else []
                    )
                    server_page_provider_type = str(
                        embed_scope.get("provider_type", "") or ""
                    ).strip().lower()
                    server_page_provider_instance = str(
                        embed_scope.get("provider_instance", "") or ""
                    ).strip()
            if server_page_skill_scope:
                # The server-restored page projection is authoritative for this
                # turn. A hidden object action starts a new page workflow, so
                # discard an earlier trace then. A visible follow-up may reuse
                # only metadata produced after the latest hidden action when it
                # was bound to this exact server-owned context identity.
                if server_page_action_start:
                    target_md_skill_workflow_context = None
                else:
                    scoped_workflow_history = _embed_scope_workflow_history(
                        message_history,
                        embed_scope=embed_scope,
                    )
                    target_md_skill_workflow_context = (
                        build_target_md_skill_workflow_context(
                            recent_history=scoped_workflow_history,
                        )
                        if scoped_workflow_history is not None
                        else None
                    )
                provider_skill_targets = [
                    provider_skill_capability_name(
                        provider_name=server_page_provider_instance,
                        provider_type=server_page_provider_type,
                        qualified_skill_name=skill_ref,
                    )
                    for skill_ref in server_page_skill_refs
                ]
                provider_skill_targets = unique_capability_values(provider_skill_targets)
                provider_targets = []
                if (
                    provider_skill_targets
                    and server_page_provider_type
                    and server_page_provider_instance
                ):
                    provider_targets.append(
                        f"{server_page_provider_type}.{server_page_provider_instance}"
                    )
                provider_types = (
                    [server_page_provider_type]
                    if provider_targets
                    else []
                )
                capability_selector_intent_plan = ToolIntentPlan(
                    action=ToolIntentAction.DIRECT_ANSWER,
                    target_provider_instances=provider_targets,
                    target_provider_types=provider_types,
                    target_provider_skill_names=provider_skill_targets,
                    target_tool_names=[],
                    reason="server_page_projection",
                )
                metadata_candidates = {
                    "reason": "server_page_projection",
                    "confidence": 1.0,
                    "preferred_provider_instances": provider_targets,
                    "preferred_provider_types": provider_types,
                    "preferred_provider_skill_names": provider_skill_targets,
                    "preferred_group_ids": [],
                    "preferred_capability_classes": [],
                    "preferred_tool_names": [],
                    "preferred_skill_names": [],
                }
                _log_step(
                    "capability_selector_skipped",
                    reason="server_page_projection",
                    target_provider_instances=provider_targets,
                    target_provider_skill_names=provider_skill_targets,
                    target_tool_names=[],
                )
            elif selected_tool_intent_plan is not None:
                if _selected_plan_matches_active_capability(
                    intent_plan=selected_tool_intent_plan,
                    active_provider_skill=transcript_active_provider_skill,
                    active_skill=transcript_active_skill,
                ):
                    resolved_tool_request, resolved_follow_up_context = (
                        self._build_active_capability_continuation_request(
                            user_message=user_message,
                            recent_history=message_history,
                        )
                    )
                    tool_request_message = resolved_tool_request
                    used_follow_up_context = resolved_follow_up_context
                    model_user_message = (
                        tool_request_message
                        if resolved_follow_up_context and tool_request_message != user_message
                        else user_message
                    )
                    selected_tool_intent_plan = selected_tool_intent_plan.model_copy(
                        update={
                            "action": ToolIntentAction.DIRECT_ANSWER,
                            "selector_outcome": CapabilitySelectorOutcome.AUTHORIZED_CONTEXT,
                            "reason": "user_selected_capability_active_continuation",
                        }
                    )
                    _log_step(
                        "user_selected_capability_active_continuation",
                        target_provider_skill_names=list(
                            selected_tool_intent_plan.target_provider_skill_names
                        ),
                        target_skill_names=list(selected_tool_intent_plan.target_skill_names),
                        used_follow_up_context=used_follow_up_context,
                    )
                metadata_candidates = {
                    "reason": "user_selected_capability",
                    "confidence": 1.0,
                    "preferred_provider_instances": list(
                        selected_tool_intent_plan.target_provider_instances
                    ),
                    "preferred_provider_types": list(selected_tool_intent_plan.target_provider_types),
                    "preferred_provider_skill_names": list(
                        selected_tool_intent_plan.target_provider_skill_names
                    ),
                    "preferred_group_ids": list(selected_tool_intent_plan.target_group_ids),
                    "preferred_capability_classes": list(
                        selected_tool_intent_plan.target_capability_classes
                    ),
                    "preferred_tool_names": list(selected_tool_intent_plan.target_tool_names),
                    "preferred_skill_names": list(selected_tool_intent_plan.target_skill_names),
                }
                _log_step(
                    "user_selected_capability_applied",
                    target_provider_instances=list(
                        selected_tool_intent_plan.target_provider_instances
                    ),
                    target_provider_types=list(selected_tool_intent_plan.target_provider_types),
                    target_provider_skill_names=list(
                        selected_tool_intent_plan.target_provider_skill_names
                    ),
                    target_skill_names=list(selected_tool_intent_plan.target_skill_names),
                    target_tool_names=list(selected_tool_intent_plan.target_tool_names),
                )
            else:
                filtered_tools, hidden_memory_tools = filter_implicit_memory_tools(available_tools)
                if hidden_memory_tools:
                    available_tools = filtered_tools
                    runtime_allowed_tool_names = [
                        str(tool.get("name", "") or "").strip()
                        for tool in available_tools
                        if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
                    ]
                    if isinstance(deps.extra, dict):
                        deps.extra["tools_snapshot"] = list(available_tools)
                        deps.extra["tools_snapshot_authoritative"] = True
                        deps.extra["runtime_allowed_tool_names"] = list(runtime_allowed_tool_names)
                        deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                            tool_groups_snapshot,
                            available_tools,
                        )
                    _log_step(
                        "implicit_memory_tools_hidden",
                        reason="memory_read_tools_require_explicit_slash_or_internal_services",
                        removed_tools=hidden_memory_tools,
                    )
                has_transcript_active_capability = bool(
                    transcript_active_provider_skill or transcript_active_skill
                )
                if has_transcript_active_capability:
                    tool_request_message = user_message
                    model_user_message = user_message
                    used_follow_up_context = False
                    _log_step(
                        "active_capability_context_forwarded",
                        reason="single_capability_selector_pass",
                        active_provider_skill=transcript_active_provider_skill or "",
                        active_skill=transcript_active_skill or "",
                    )
                else:
                    tool_request_message, used_follow_up_context = (
                        self._resolve_contextual_tool_request(
                            user_message=user_message,
                            recent_history=message_history,
                            deps=deps,
                        )
                    )
                    model_user_message = (
                        tool_request_message
                        if used_follow_up_context and tool_request_message != user_message
                        else user_message
                    )

                if isinstance(deps.extra, dict):
                    if used_follow_up_context and tool_request_message != user_message:
                        deps.extra["current_follow_up_context"] = tool_request_message
                    else:
                        deps.extra.pop("current_follow_up_context", None)
                _log_step(
                    "tool_request_resolved",
                    used_follow_up_context=used_follow_up_context,
                    raw_user_message=user_message,
                    resolved_tool_request=tool_request_message,
                )
                if capability_selector_intent_plan is None:
                    usage_profile_context = ""
                    usage_profile_result = await self.active_memory.recall_usage_profile_for_routing(
                        deps=deps,
                        session_key=session_key,
                    )
                    if isinstance(deps.extra, dict):
                        deps.extra["usage_profile_routing"] = {
                            "status": str(getattr(usage_profile_result, "status", "") or ""),
                            "elapsed_ms": int(
                                getattr(usage_profile_result, "elapsed_ms", 0) or 0
                            ),
                            "result_count": int(
                                getattr(usage_profile_result, "result_count", 0) or 0
                            ),
                        }
                    usage_profile_context = str(
                        getattr(usage_profile_result, "context", "") or ""
                    ).strip()
                    if usage_profile_context:
                        _log_step(
                            "usage_profile_routing_hints_injected",
                            status=str(getattr(usage_profile_result, "status", "") or ""),
                            result_count=int(
                                getattr(usage_profile_result, "result_count", 0) or 0
                            ),
                            elapsed_ms=int(getattr(usage_profile_result, "elapsed_ms", 0) or 0),
                        )
                    selector_user_message = tool_request_message
                    active_capability_context = (
                        self._format_active_capability_name(
                            active_provider_skill=transcript_active_provider_skill or "",
                            active_skill=transcript_active_skill or "",
                        )
                        if has_transcript_active_capability
                        else ""
                    )
                    capability_selector_intent_plan = await self._select_capability_intent_plan_with_model(
                        agent=runtime_agent or self.agent,
                        deps=deps,
                        user_message=selector_user_message,
                        recent_history=message_history,
                        capability_index=capability_index,
                        usage_profile_context=usage_profile_context,
                        active_capability_context=active_capability_context,
                    )
                    if capability_selector_intent_plan is None:
                        capability_selector_failed = True
                        capability_selector_intent_plan = ToolIntentPlan(
                            action=ToolIntentAction.DIRECT_ANSWER,
                            reason="capability_selector_returned_no_valid_target",
                        )
                metadata_candidates = {
                    "reason": "llm_capability_selector",
                    "confidence": (
                        1.0
                        if capability_selector_intent_plan.reason
                        != "capability_selector_returned_no_valid_target"
                        else 0.0
                    ),
                    "preferred_provider_instances": (
                        list(capability_selector_intent_plan.target_provider_instances)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_provider_types": (
                        list(capability_selector_intent_plan.target_provider_types)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_provider_skill_names": (
                        list(capability_selector_intent_plan.target_provider_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_group_ids": (
                        list(capability_selector_intent_plan.target_group_ids)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_capability_classes": (
                        list(capability_selector_intent_plan.target_capability_classes)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_tool_names": (
                        list(capability_selector_intent_plan.target_tool_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    "preferred_skill_names": (
                        list(capability_selector_intent_plan.target_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                }
                _log_step(
                    "capability_selector_resolved",
                    enabled=capability_selector_intent_plan is not None,
                    action=(
                        capability_selector_intent_plan.action.value
                        if capability_selector_intent_plan is not None
                        else ""
                    ),
                    unavailable_runtime_capability=bool(
                        capability_selector_intent_plan
                        and capability_selector_intent_plan.unavailable_runtime_capability
                    ),
                    target_provider_instances=(
                        list(capability_selector_intent_plan.target_provider_instances)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_provider_types=(
                        list(capability_selector_intent_plan.target_provider_types)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_provider_skill_names=(
                        list(capability_selector_intent_plan.target_provider_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_skill_names=(
                        list(capability_selector_intent_plan.target_skill_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                    target_tool_names=(
                        list(capability_selector_intent_plan.target_tool_names)
                        if capability_selector_intent_plan is not None
                        else []
                    ),
                )
            ranking_trace = {
                "status": (
                    "server_page_projection"
                    if metadata_candidates.get("reason") == "server_page_projection"
                    else "capability_selector"
                ),
                "reason": str(metadata_candidates.get("reason", "") or "capability_selector"),
                "confidence": float(metadata_candidates.get("confidence", 0.0) or 0.0),
                "preferred_provider_instances": list(
                    metadata_candidates.get("preferred_provider_instances", []) or []
                ),
                "preferred_provider_types": list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                "preferred_provider_skill_names": list(
                    metadata_candidates.get("preferred_provider_skill_names", []) or []
                ),
                "preferred_group_ids": list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                "preferred_capability_classes": list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                "preferred_tool_names": list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            }
            if isinstance(deps.extra, dict):
                deps.extra["tool_metadata_candidates"] = dict(metadata_candidates)
                deps.extra["tool_ranking_trace"] = dict(ranking_trace)
            _log_step(
                (
                    "server_page_projection_recorded"
                    if metadata_candidates.get("reason") == "server_page_projection"
                    else "capability_selector_recorded"
                ),
                confidence=float(metadata_candidates.get("confidence", 0.0) or 0.0),
                preferred_provider_instances=list(
                    metadata_candidates.get("preferred_provider_instances", []) or []
                ),
                preferred_provider_types=list(
                    metadata_candidates.get("preferred_provider_types", []) or []
                ),
                preferred_provider_skill_names=list(
                    metadata_candidates.get("preferred_provider_skill_names", []) or []
                ),
                preferred_group_ids=list(
                    metadata_candidates.get("preferred_group_ids", []) or []
                ),
                preferred_capability_classes=list(
                    metadata_candidates.get("preferred_capability_classes", []) or []
                ),
                preferred_tool_names=list(
                    metadata_candidates.get("preferred_tool_names", []) or []
                ),
            )
            if metadata_candidates.get("reason") == "server_page_projection":
                metadata_tool_intent_plan = capability_selector_intent_plan
            elif selected_tool_intent_plan is not None:
                metadata_tool_intent_plan = selected_tool_intent_plan
            else:
                metadata_tool_intent_plan = capability_selector_intent_plan
            metadata_tool_intent_plan, provider_instance_selection_trace = (
                apply_provider_instance_selection_policy(
                    deps=deps,
                    intent_plan=metadata_tool_intent_plan,
                )
            )
            if provider_instance_selection_trace.get("enabled"):
                metadata_candidates["preferred_provider_instances"] = list(
                    metadata_tool_intent_plan.target_provider_instances
                    if metadata_tool_intent_plan is not None
                    else []
                )
                metadata_candidates["preferred_provider_types"] = list(
                    metadata_tool_intent_plan.target_provider_types
                    if metadata_tool_intent_plan is not None
                    else []
                )
                _log_step(
                    "provider_instance_selection_applied",
                    selected_provider_instances=list(
                        provider_instance_selection_trace.get(
                            "selected_provider_instances", []
                        )
                        or []
                    ),
                    target_provider_types=list(
                        provider_instance_selection_trace.get("target_provider_types", [])
                        or []
                    ),
                )
                persisted_provider_instances = (
                    await persist_provider_instance_targets_from_intent_plan(
                        deps=deps,
                        intent_plan=metadata_tool_intent_plan,
                    )
                )
                if persisted_provider_instances:
                    _log_step(
                        "provider_instance_selection_persisted",
                        selected_provider_instances=persisted_provider_instances,
                    )
            if metadata_tool_intent_plan is not None:
                _log_step(
                    (
                        "server_page_projection_plan_resolved"
                        if metadata_candidates.get("reason") == "server_page_projection"
                        else "capability_selector_plan_resolved"
                    ),
                    action=metadata_tool_intent_plan.action.value,
                    target_provider_instances=list(
                        metadata_tool_intent_plan.target_provider_instances
                    ),
                    target_provider_types=list(metadata_tool_intent_plan.target_provider_types),
                    target_provider_skill_names=list(
                        metadata_tool_intent_plan.target_provider_skill_names
                    ),
                    target_skill_names=list(metadata_tool_intent_plan.target_skill_names),
                    target_capability_classes=list(metadata_tool_intent_plan.target_capability_classes),
                    target_tool_names=list(metadata_tool_intent_plan.target_tool_names),
                )
            if selected_tool_intent_plan is not None:
                explicit_capability_match = True
                tool_intent_plan = metadata_tool_intent_plan
            else:
                tool_intent_plan = metadata_tool_intent_plan
                explicit_capability_match = bool(
                    tool_intent_plan is not None
                    and any(
                        [
                            list(tool_intent_plan.target_provider_instances or []),
                            list(tool_intent_plan.target_provider_types or []),
                            list(tool_intent_plan.target_provider_skill_names or []),
                            list(tool_intent_plan.target_skill_names or []),
                            list(tool_intent_plan.target_group_ids or []),
                            list(tool_intent_plan.target_capability_classes or []),
                            list(tool_intent_plan.target_tool_names or []),
                        ]
                    )
                )
            artifact_goal = resolve_artifact_goal_from_intent_plan(tool_intent_plan)
            if isinstance(deps.extra, dict):
                if artifact_goal is not None:
                    deps.extra["artifact_goal"] = dict(artifact_goal)
                else:
                    deps.extra.pop("artifact_goal", None)
            _log_step(
                "artifact_goal_resolved",
                artifact_kind=str((artifact_goal or {}).get("kind", "") or ""),
                artifact_label=str((artifact_goal or {}).get("label", "") or ""),
                source="runtime_intent_plan",
            )
            if isinstance(deps.extra, dict):
                if tool_intent_plan is not None:
                    deps.extra["tool_intent_plan"] = tool_intent_plan.model_dump(mode="python")
                else:
                    deps.extra.pop("tool_intent_plan", None)
            _log_step(
                "routing_guidance_built",
                enabled=tool_intent_plan is not None,
                action=(
                    tool_intent_plan.action.value
                    if tool_intent_plan is not None
                    else ""
                ),
                target_provider_instances=list(tool_intent_plan.target_provider_instances or [])
                if tool_intent_plan is not None
                else [],
                target_provider_types=list(tool_intent_plan.target_provider_types or [])
                if tool_intent_plan is not None
                else [],
                target_provider_skill_names=list(
                    tool_intent_plan.target_provider_skill_names or []
                )
                if tool_intent_plan is not None
                else [],
                target_skill_names=list(tool_intent_plan.target_skill_names or [])
                if tool_intent_plan is not None
                else [],
                target_group_ids=list(tool_intent_plan.target_group_ids or [])
                if tool_intent_plan is not None
                else [],
                target_capability_classes=list(tool_intent_plan.target_capability_classes or [])
                if tool_intent_plan is not None
                else [],
                target_tool_names=list(tool_intent_plan.target_tool_names or [])
                if tool_intent_plan is not None
                else [],
                missing_inputs=list(tool_intent_plan.missing_inputs or [])
                if tool_intent_plan is not None
                else [],
            )
            if not explicit_capability_match:
                _log_step(
                    "unmatched_intent_tools_preserved",
                    reason="capability_selector_returned_no_target_preserve_authorized_tools",
                    available_tool_count=len(available_tools),
                )

            if tool_intent_plan is not None:
                tool_gate_decision = self._normalize_tool_gate_decision(
                    self._build_tool_gate_decision_from_intent_plan(
                        tool_intent_plan,
                        available_tools=available_tools,
                    )
                )
            else:
                tool_gate_decision = self._normalize_tool_gate_decision(
                    ToolGateDecision(
                        needs_tool=False,
                        reason=(
                            "LLM-first runtime routing is active. The main model decides this "
                            "turn after capability pruning."
                        ),
                        policy=ToolPolicyMode.ANSWER_DIRECT,
                    )
                )
            pre_projection_has_executable_tools = bool(
                available_tools
            ) and not toolset_has_only_coordination_support_tools(available_tools)
            available_tools, tool_projection_trace = project_minimal_toolset(
                allowed_tools=available_tools,
                intent_plan=tool_intent_plan,
            )
            runtime_visible_tools = list(available_tools)
            provider_instance_pruning_trace: dict[str, Any] = {}
            runtime_visible_tools, provider_instance_pruning_trace = (
                prune_auto_selected_provider_instance_tools(
                    available_tools=runtime_visible_tools,
                    deps=deps,
                    intent_plan=tool_intent_plan,
                )
            )
            if provider_instance_pruning_trace.get("enabled"):
                _log_step(
                    "provider_instance_tools_pruned",
                    removed_tools=list(
                        provider_instance_pruning_trace.get("removed_tools", []) or []
                    ),
                    target_provider_types=list(
                        provider_instance_pruning_trace.get("target_provider_types", []) or []
                    ),
                    auto_selected_provider_types=list(
                        provider_instance_pruning_trace.get(
                            "auto_selected_provider_types", []
                        )
                        or []
                    ),
                )
            runtime_allowed_tool_names = [
                str(tool.get("name", "") or "").strip()
                for tool in runtime_visible_tools
                if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
            ]
            available_tools = runtime_visible_tools
            if isinstance(deps.extra, dict):
                deps.extra["tool_projection_trace"] = dict(tool_projection_trace)
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["runtime_allowed_tool_names"] = list(runtime_allowed_tool_names)
                deps.extra["provider_instance_pruning_trace"] = dict(
                    provider_instance_pruning_trace
                )
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            _log_step(
                "tool_projection_applied",
                before_count=int(tool_projection_trace.get("before_count", 0) or 0),
                after_count=int(tool_projection_trace.get("after_count", 0) or 0),
                runtime_visible_count=len(available_tools),
                reason=str(tool_projection_trace.get("reason", "") or ""),
                coordination_tools=list(tool_projection_trace.get("coordination_tools", []) or []),
            )
            target_md_skill = None
            # ── SKILL.md resolution ──────────────────────────────────────
            # SKILL.md loading follows the selected routing plan as-is. Runtime
            # does not force tool execution from transcript evidence alone.
            #
            # Webhook-selected skills are an authenticated routing decision, so
            # they take precedence over the model-selected routing plan.
            preselected_md_skill_plan = build_preselected_md_skill_intent_plan(deps)
            if preselected_md_skill_plan is not None:
                _log_step(
                    "target_md_skill_preselected",
                    reason=preselected_md_skill_plan.reason,
                    target_provider_instances=list(
                        preselected_md_skill_plan.target_provider_instances
                    ),
                    target_provider_types=list(preselected_md_skill_plan.target_provider_types),
                    target_provider_skill_names=list(
                        preselected_md_skill_plan.target_provider_skill_names
                    ),
                    target_skill_names=list(preselected_md_skill_plan.target_skill_names),
                )
            routed_skill_resolution_plan = (
                tool_intent_plan if should_resolve_target_md_skill(tool_intent_plan) else None
            )
            skill_resolution_plan = (
                preselected_md_skill_plan
                or routed_skill_resolution_plan
            )
            if should_resolve_target_md_skill(skill_resolution_plan):
                target_md_skill = resolve_selected_md_skill_target(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                    intent_plan=skill_resolution_plan,
                    max_file_bytes=int(
                        getattr(self.prompt_builder.config, "md_skills_max_file_bytes", 262144)
                        or 262144
                    ),
                )
            target_md_skill = enrich_target_md_skill_with_workflow_context(
                target_md_skill=target_md_skill,
                workflow_trace=target_md_skill_workflow_context,
            )
            if isinstance(deps.extra, dict):
                if isinstance(target_md_skill, dict):
                    deps.extra["target_md_skill"] = dict(target_md_skill)
                else:
                    deps.extra.pop("target_md_skill", None)
                # Store active trace ID so tool execution can inject it as env var
                if isinstance(target_md_skill_workflow_context, dict):
                    _active_trace = target_md_skill_workflow_context.get(
                        "internal_request_trace_id"
                    )
                    if _active_trace:
                        deps.extra["active_internal_request_trace_id"] = _active_trace
                    else:
                        deps.extra.pop("active_internal_request_trace_id", None)
                else:
                    deps.extra.pop("active_internal_request_trace_id", None)
            available_tools, standard_skill_runtime_trace, target_md_skill = (
                inject_standard_skill_runtime_tools(
                    available_tools=available_tools,
                    deps=deps,
                    target_md_skill=target_md_skill,
                    execution_allowed=not (
                        tool_intent_plan is not None
                        and tool_intent_plan.selector_outcome
                        is CapabilitySelectorOutcome.AUTHORIZED_CONTEXT
                    ),
                )
            )
            runtime_allowed_tool_names = [
                str(tool.get("name", "") or "").strip()
                for tool in available_tools
                if isinstance(tool, dict) and str(tool.get("name", "") or "").strip()
            ]
            if isinstance(deps.extra, dict):
                deps.extra["tools_snapshot"] = list(available_tools)
                deps.extra["tools_snapshot_authoritative"] = True
                deps.extra["runtime_allowed_tool_names"] = list(runtime_allowed_tool_names)
                deps.extra["standard_skill_runtime_trace"] = dict(
                    standard_skill_runtime_trace
                )
                deps.extra["tool_groups_snapshot"] = self._build_filtered_group_map(
                    tool_groups_snapshot,
                    available_tools,
                )
            _log_step(
                "standard_skill_runtime_injected",
                enabled=bool(standard_skill_runtime_trace.get("enabled")),
                reason=str(standard_skill_runtime_trace.get("reason", "") or ""),
                tool_names=list(standard_skill_runtime_trace.get("tool_names", []) or []),
                runtime_visible_count=len(available_tools),
            )
            _log_step(
                "target_md_skill_resolved",
                enabled=bool(target_md_skill),
                provider_skill_name=(
                    str(target_md_skill.get("provider_skill_name", "") or "")
                    if isinstance(target_md_skill, dict)
                    else ""
                ),
                qualified_name=(
                    ""
                    if isinstance(target_md_skill, dict)
                    and str(target_md_skill.get("provider_skill_name", "") or "").strip()
                    else (
                        str(target_md_skill.get("qualified_name", "") or "")
                        if isinstance(target_md_skill, dict)
                        else ""
                    )
                ),
                loaded_instructions=bool(
                    isinstance(target_md_skill, dict)
                    and str(target_md_skill.get("instructions", "") or "").strip()
                ),
                workflow_context_entries=len(
                    (
                        target_md_skill.get("workflow_context", {}).get(
                            "recent_tool_metadata", []
                        )
                        if isinstance(target_md_skill, dict)
                        else []
                    )
                ),
            )
            if (
                turn_action_requires_tool_execution(tool_intent_plan)
                and not bool(standard_skill_runtime_trace.get("enabled"))
                and toolset_has_only_coordination_support_tools(available_tools)
            ):
                available_tools = []
                tool_intent_plan = ToolIntentPlan(
                    action=ToolIntentAction.DIRECT_ANSWER,
                    unavailable_runtime_capability=True,
                    reason=(
                        "No executable provider, skill, or tool is available; "
                        "coordination-only helpers are not enough to perform this request."
                    ),
                )
                tool_gate_decision = self._build_tool_gate_decision_from_intent_plan(
                    tool_intent_plan,
                    available_tools=available_tools,
                )
                runtime_allowed_tool_names = []
                if isinstance(deps.extra, dict):
                    deps.extra["tools_snapshot"] = []
                    deps.extra["tools_snapshot_authoritative"] = True
                    deps.extra["runtime_allowed_tool_names"] = []
                    deps.extra["tool_groups_snapshot"] = {}
                _log_step(
                    "coordination_only_toolset_dropped",
                    reason="no_executable_runtime_capability",
                )
            if (
                capability_selector_failed
                and not pre_projection_has_executable_tools
                and tool_intent_plan is not None
                and tool_intent_plan.action
                in {ToolIntentAction.DIRECT_ANSWER, ToolIntentAction.ASK_CLARIFICATION}
                and not _intent_plan_has_explicit_targets(tool_intent_plan)
            ):
                no_capability_route = await self._classify_no_capability_route_with_model(
                    agent=runtime_agent or self.agent,
                    deps=deps,
                    user_message=user_message,
                    recent_history=message_history,
                )
                if no_capability_route == self.NO_CAPABILITY_RUNTIME_REQUEST:
                    tool_intent_plan = ToolIntentPlan(
                        action=ToolIntentAction.DIRECT_ANSWER,
                        unavailable_runtime_capability=True,
                        reason="fallback_classifier_detected_unavailable_runtime_capability",
                    )
                    tool_gate_decision = self._build_tool_gate_decision_from_intent_plan(
                        tool_intent_plan,
                        available_tools=available_tools,
                    )
                _log_step(
                    "no_capability_fallback_resolved",
                    decision=no_capability_route or "",
                    applied=bool(tool_intent_plan.unavailable_runtime_capability),
                )
            tool_match_result = CapabilityMatcher(available_tools=available_tools).match(
                tool_gate_decision.suggested_tool_classes
            )
            logger.warning(
                "tool_intent decision: session=%s action=%s policy=%s needs_external=%s needs_live_data=%s suggested=%s candidates=%s",
                session_key,
                tool_intent_plan.action.value if tool_intent_plan is not None else "llm_first",
                tool_gate_decision.policy.value,
                bool(tool_gate_decision.needs_external_system),
                bool(tool_gate_decision.needs_live_data),
                list(tool_gate_decision.suggested_tool_classes),
                [
                    str(getattr(candidate, "name", "") or "").strip()
                    for candidate in tool_match_result.tool_candidates
                    if str(getattr(candidate, "name", "") or "").strip()
                ],
            )
            _log_step(
                "tool_gate_decided",
                action=tool_intent_plan.action.value if tool_intent_plan is not None else "llm_first",
                policy=tool_gate_decision.policy.value,
                needs_tool=bool(tool_gate_decision.needs_tool),
                needs_external=bool(tool_gate_decision.needs_external_system),
                needs_live_data=bool(tool_gate_decision.needs_live_data),
                suggested_classes=list(tool_gate_decision.suggested_tool_classes),
                candidate_count=len(tool_match_result.tool_candidates),
                missing_capabilities=list(tool_match_result.missing_capabilities),
            )
            tool_execution_required = turn_action_requires_tool_execution(tool_intent_plan)
            reasoning_retry_limit = self.REASONING_ONLY_MAX_RETRIES
            if tool_execution_required:
                reasoning_retry_limit = 0
            self._inject_tool_policy(
                deps=deps,
                intent_plan=tool_intent_plan,
                available_tools=available_tools,
            )
            _log_step(
                "tool_policy_injected",
                tool_execution_required=tool_execution_required,
                reasoning_retry_limit=reasoning_retry_limit,
            )
            prompt_mode = select_execution_prompt_mode(
                intent_action=tool_intent_plan.action.value if tool_intent_plan is not None else "",
                is_follow_up=used_follow_up_context,
                projected_tool_count=len(available_tools),
                has_target_md_skill=bool(target_md_skill),
            )
            explicit_tool_execution_target = select_explicit_tool_execution_target(
                intent_plan=tool_intent_plan,
                is_follow_up=used_follow_up_context,
                projected_tools=available_tools,
                has_target_md_skill=bool(target_md_skill),
            )
            if isinstance(explicit_tool_execution_target, dict):
                explicit_tool_execution_target = dict(explicit_tool_execution_target)
            _log_step(
                "execution_prompt_mode_selected",
                mode="explicit_tool_execution" if explicit_tool_execution_target else prompt_mode.value,
                projected_tool_count=len(available_tools),
                used_follow_up_context=used_follow_up_context,
                explicit_tool_name=(
                    str(explicit_tool_execution_target.get("name", "") or "").strip()
                    if isinstance(explicit_tool_execution_target, dict)
                    else ""
                ),
            )
            await self.runtime_events.trigger_tool_gate_evaluated(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
            )
            await self.runtime_events.trigger_tool_matcher_resolved(
                session_key=session_key,
                run_id=run_id,
                decision=tool_gate_decision,
                match_result=tool_match_result,
            )

            if tool_execution_required and not available_tools:
                failure_message = (
                    "This turn requires real tool execution, but no executable tools remained "
                    "after policy and metadata filtering."
                )
                yield StreamEvent.runtime_update(
                    "failed",
                    failure_message,
                    metadata={"phase": "gate", "elapsed": round(time.monotonic() - start_time, 1)},
                )
                yield StreamEvent.error_event(failure_message)
                run_failed = True
                state["run_failed"] = True
                state["should_stop"] = True
                return

            if explicit_tool_execution_target is not None:
                system_prompt = build_explicit_tool_execution_prompt(
                    tool=explicit_tool_execution_target,
                )
            else:
                system_prompt = build_system_prompt(
                    self.prompt_builder,
                    session=session,
                    deps=deps,
                    agent=runtime_agent or self.agent,
                    context_window_tokens=runtime_context_window,
                    prompt_mode=prompt_mode,
                )
                consume_prompt_warnings = getattr(self.prompt_builder, "consume_warnings", None)
                if callable(consume_prompt_warnings):
                    for warning_message in consume_prompt_warnings():
                        if not self._should_surface_prompt_warning(warning_message):
                            logger.debug("Suppressing prompt-context warning: %s", warning_message)
                            continue
                        yield StreamEvent.runtime_update(
                            "warning",
                            warning_message,
                            metadata={
                                "phase": "prompt_context",
                                "elapsed": round(time.monotonic() - start_time, 1),
                            },
                        )

            if self.hooks:
                prompt_ctx = await self.hooks.trigger(
                    "before_prompt_build",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                        "system_prompt": system_prompt,
                    },
                )
                system_prompt = prompt_ctx.get("system_prompt", system_prompt)

            active_memory_allowed_for_turn = (
                explicit_tool_execution_target is None
                and not tool_execution_required
                and not bool(getattr(tool_gate_decision, "needs_tool", False))
            )
            if active_memory_allowed_for_turn:
                # Active memory is intentionally injected only after tool
                # routing has declined to act. That keeps user preferences from
                # changing skill/provider/tool selection while still allowing
                # response UX adaptation for direct answers.
                active_memory = getattr(self, "active_memory", None)
                if active_memory is not None:
                    try:
                        active_memory_result = await active_memory.recall(
                            deps=deps,
                            session_key=session_key,
                            user_message=user_message,
                        )
                    except Exception as exc:
                        logger.warning("active memory recall failed open: %s", exc)
                        active_memory_result = None
                    if active_memory_result is not None and isinstance(deps.extra, dict):
                        deps.extra["active_memory"] = {
                            "status": str(getattr(active_memory_result, "status", "") or ""),
                            "elapsed_ms": int(getattr(active_memory_result, "elapsed_ms", 0) or 0),
                            "result_count": int(getattr(active_memory_result, "result_count", 0) or 0),
                        }
                    active_context = (
                        str(getattr(active_memory_result, "context", "") or "").strip()
                        if active_memory_result is not None
                        else ""
                    )
                    if active_context:
                        system_prompt = f"{active_context}\n\n{system_prompt}"
                        _log_step(
                            "active_memory_injected",
                            status=str(getattr(active_memory_result, "status", "") or ""),
                            result_count=int(getattr(active_memory_result, "result_count", 0) or 0),
                            elapsed_ms=int(getattr(active_memory_result, "elapsed_ms", 0) or 0),
                        )
                    elif active_memory_result is not None:
                        _log_step(
                            "active_memory_skipped",
                            status=str(getattr(active_memory_result, "status", "") or ""),
                            elapsed_ms=int(getattr(active_memory_result, "elapsed_ms", 0) or 0),
                        )
            elif explicit_tool_execution_target is None and isinstance(deps.extra, dict):
                deps.extra["active_memory"] = {
                    "status": "tool_selection_skipped",
                    "elapsed_ms": 0,
                    "result_count": 0,
                }
                _log_step("active_memory_skipped", status="tool_selection_skipped", elapsed_ms=0)

            if message_history and self.compaction.should_compact(
                message_history,
                session,
                context_window_override=runtime_context_window,
            ):
                if self.hooks:
                    await self.hooks.trigger(
                        "before_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )
                yield StreamEvent.compaction_start()
                compressed_history = await self.compaction.compact(message_history, session)
                message_history = self.history.normalize_messages(compressed_history)
                context_history_for_hooks = list(message_history)
                await session_manager.mark_compacted(session_key)
                compaction_applied = True
                yield StreamEvent.compaction_end()
                if self.hooks:
                    await self.hooks.trigger(
                        "after_compaction",
                        {
                            "session_key": session_key,
                            "message_count": len(message_history),
                        },
                    )

            # -- hook:before_agent_start --
            if self.hooks:
                start_ctx = await self.hooks.trigger(
                    "before_agent_start",
                    {
                        "session_key": session_key,
                        "user_message": user_message,
                    },
                )
                user_message = start_ctx.get("user_message", user_message)
            session_message_history = list(message_history)
            runtime_message_history = self._build_runtime_message_history_for_turn(
                session_message_history=session_message_history,
                used_follow_up_context=used_follow_up_context,
                intent_plan=tool_intent_plan,
            )
            runtime_base_history_len = len(runtime_message_history)
            persist_run_output_start_index = len(session_message_history)
            if runtime_base_history_len != len(session_message_history):
                _log_step(
                    "runtime_message_history_trimmed",
                    session_history_count=len(session_message_history),
                    runtime_history_count=runtime_base_history_len,
                    used_follow_up_context=used_follow_up_context,
                    action=getattr(tool_intent_plan, "action", None).value if tool_intent_plan else "",
                )
        finally:
            resolved_runtime_message_history = (
                list(runtime_message_history)
                if runtime_message_history is not None
                else list(message_history)
            )
            resolved_session_message_history = (
                list(session_message_history)
                if session_message_history is not None
                else list(message_history)
            )
            state.update({
                "session_key": session_key,
                "user_message": user_message,
                "deps": deps,
                "max_tool_calls": max_tool_calls,
                "timeout_seconds": timeout_seconds,
                "_token_failover_attempt": _token_failover_attempt,
                "_emit_lifecycle_bounds": _emit_lifecycle_bounds,
                "start_time": start_time,
                "tool_calls_count": tool_calls_count,
                "compaction_applied": compaction_applied,
                "thinking_emitter": thinking_emitter,
                "persist_override_messages": persist_override_messages,
                "persist_override_base_len": persist_override_base_len,
                "runtime_agent": runtime_agent,
                "selected_token_id": selected_token_id,
                "release_slot": release_slot,
                "extra": extra,
                "run_id": run_id,
                "tool_execution_retry_count": tool_execution_retry_count,
                # Any failure marker set during the run (including by this
                # phase writing state directly) must survive the write-back;
                # a stale local used to overwrite it and lose the failure.
                "run_failed": bool(run_failed) or bool(state.get("run_failed")),
                "message_history": message_history,
                "runtime_message_history": resolved_runtime_message_history,
                "session_message_history": resolved_session_message_history,
                "runtime_base_history_len": runtime_base_history_len if runtime_base_history_len is not None else len(resolved_runtime_message_history),
                "persist_run_output_start_index": persist_run_output_start_index if persist_run_output_start_index is not None else len(message_history),
                "system_prompt": system_prompt,
                "final_assistant": final_assistant,
                "context_history_for_hooks": context_history_for_hooks,
                "tool_call_summaries": tool_call_summaries,
                "session_title": session_title,
                "buffered_assistant_events": buffered_assistant_events,
                "assistant_output_streamed": assistant_output_streamed,
                "tool_request_message": tool_request_message,
                "model_user_message": model_user_message,
                "tool_intent_plan": tool_intent_plan,
                "tool_gate_decision": tool_gate_decision,
                "tool_match_result": tool_match_result,
                "current_model_attempt": current_model_attempt,
                "current_attempt_started_at": current_attempt_started_at,
                "current_attempt_has_text": current_attempt_has_text,
                "current_attempt_has_tool": current_attempt_has_tool,
                "reasoning_retry_count": reasoning_retry_count,
                "run_output_start_index": run_output_start_index,
                "tool_execution_required": tool_execution_required,
                "buffer_direct_answer_output": (not tool_execution_required and not bool(available_tools)),
                "reasoning_retry_limit": reasoning_retry_limit,
                "model_stream_timed_out": model_stream_timed_out,
                "model_timeout_error_message": model_timeout_error_message,
                "runtime_context_window_info": runtime_context_window_info,
                "runtime_context_guard": runtime_context_guard,
                "runtime_context_window": runtime_context_window,
                "session_manager": session_manager,
                "session": session,
                "transcript": transcript,
                "all_available_tools": all_available_tools,
                "tool_groups_snapshot": tool_groups_snapshot,
                "available_tools": available_tools,
                "toolset_filter_trace": toolset_filter_trace,
                "tool_projection_trace": tool_projection_trace,
                "used_toolset_fallback": used_toolset_fallback,
                "metadata_candidates": metadata_candidates,
                "ranking_trace": ranking_trace,
                "artifact_goal": artifact_goal,
                "prompt_mode": prompt_mode,
            })

    @staticmethod
    def _build_runtime_message_history_for_turn(
        *,
        session_message_history: list[dict[str, Any]],
        used_follow_up_context: bool,
        intent_plan: ToolIntentPlan | None,
    ) -> list[dict[str, Any]]:
        if not session_message_history:
            return []
        return list(session_message_history)
