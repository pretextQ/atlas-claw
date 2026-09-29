# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Workflow-context and target-skill pruning helpers for the prepare phase.

Extracted from runner_execution_prepare.py; the original module re-exports
every name below, so existing imports keep working.
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


from app.atlasclaw.agent.runner_tool.runner_prepare_md_skill import (  # noqa: E402
    _artifact_classes_for_entry,
    _normalize_text,
    _split_provider_instance_ref,
)




def _match_selected_md_skill_entry(
    *,
    entry: dict[str, Any],
    selected_capability_ids: set[str],
    target_provider_instances: set[str],
    target_provider_skill_names: set[str],
    target_skill_names: set[str],
    target_tool_names: set[str],
    target_capability_classes: set[str],
) -> bool:
    kind = _normalize_text(entry.get("kind", "")).lower()
    capability_id = _normalize_text(entry.get("capability_id", "")).lower()
    name = _normalize_text(entry.get("name", "")).lower()
    entry_provider_instances = {
        _normalize_text(item).lower()
        for item in (entry.get("target_provider_instances", []) or [])
        if _normalize_text(item)
    }
    entry_target_provider_skill_names = {
        _normalize_text(item).lower()
        for item in (entry.get("target_provider_skill_names", []) or [])
        if _normalize_text(item)
    }
    qualified_skill_name = _normalize_text(entry.get("qualified_skill_name", "")).lower()
    skill_name = _normalize_text(entry.get("skill_name", "")).lower()
    entry_target_skill_names = {
        _normalize_text(item).lower()
        for item in (entry.get("target_skill_names", []) or [])
        if _normalize_text(item)
    }
    declared_tool_names = {
        _normalize_text(item).lower()
        for item in (entry.get("declared_tool_names", []) or [])
        if _normalize_text(item)
    }
    artifact_classes = _artifact_classes_for_entry(entry)

    if kind == "provider_skill":
        if not entry_provider_instances.intersection(target_provider_instances):
            return False
        if capability_id and capability_id in selected_capability_ids:
            return True
        if name and name in target_provider_skill_names:
            return True
        if entry_target_provider_skill_names and entry_target_provider_skill_names.intersection(
            target_provider_skill_names
        ):
            return True
        return False
    if capability_id and capability_id in selected_capability_ids:
        return True
    if name and name in target_skill_names:
        return True
    if qualified_skill_name and qualified_skill_name in target_skill_names:
        return True
    if skill_name and skill_name in target_skill_names:
        return True
    if entry_target_skill_names and entry_target_skill_names.intersection(target_skill_names):
        return True
    if declared_tool_names and declared_tool_names.intersection(target_tool_names):
        return True
    if artifact_classes and artifact_classes.intersection(target_capability_classes):
        return True
    return False


def _rank_selected_md_skill_entry(
    *,
    entry: dict[str, Any],
    original_index: int,
    selected_capability_ids: set[str],
    target_provider_skill_order: dict[str, int],
    target_skill_order: dict[str, int],
    target_tool_order: dict[str, int],
    target_capability_classes: set[str],
) -> tuple[int, int, int, int, int, int, int]:
    capability_id = _normalize_text(entry.get("capability_id", "")).lower()
    name = _normalize_text(entry.get("name", "")).lower()
    qualified_skill_name = _normalize_text(entry.get("qualified_skill_name", "")).lower()
    skill_name = _normalize_text(entry.get("skill_name", "")).lower()
    entry_target_skill_names = [
        _normalize_text(item).lower()
        for item in (entry.get("target_skill_names", []) or [])
        if _normalize_text(item)
    ]
    entry_target_provider_skill_names = [
        _normalize_text(item).lower()
        for item in (entry.get("target_provider_skill_names", []) or [])
        if _normalize_text(item)
    ]
    declared_tool_names = [
        _normalize_text(item).lower()
        for item in (entry.get("declared_tool_names", []) or [])
        if _normalize_text(item)
    ]
    artifact_classes = _artifact_classes_for_entry(entry)

    capability_rank = 0 if capability_id and capability_id in selected_capability_ids else 1
    standard_skill_rank = 1 if bool(entry.get("declares_executable_tools")) else 0
    provider_skill_rank = min(
        (
            target_provider_skill_order.get(candidate, len(target_provider_skill_order) + 1)
            for candidate in [name, *entry_target_provider_skill_names]
            if candidate
        ),
        default=len(target_provider_skill_order) + 1,
    )
    skill_rank = min(
        (
            target_skill_order.get(candidate, len(target_skill_order) + 1)
            for candidate in [name, qualified_skill_name, skill_name, *entry_target_skill_names]
            if candidate
        ),
        default=len(target_skill_order) + 1,
    )
    tool_rank = min(
        (target_tool_order.get(item, len(target_tool_order) + 1) for item in declared_tool_names),
        default=len(target_tool_order) + 1,
    )
    artifact_rank = (
        0
        if artifact_classes and artifact_classes.intersection(target_capability_classes)
        else 1
    )
    return (
        capability_rank,
        standard_skill_rank,
        provider_skill_rank,
        skill_rank,
        tool_rank,
        artifact_rank,
        original_index,
    )


def _load_target_md_skill_full_instructions(
    *,
    file_path: str,
) -> str:
    """Load selected SKILL.md instructions for execution-stage prompting."""
    normalized_path = _normalize_text(file_path)
    if not normalized_path:
        return ""

    try:
        text = Path(normalized_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        logger.warning(
            "Failed to read skill instructions from %s; the skill prompt "
            "section will be empty",
            normalized_path,
            exc_info=True,
        )
        return ""

    text = re.sub(r"^---\s.*?---\s*", "", text, count=1, flags=re.DOTALL).strip()
    return text


def resolve_selected_md_skill_target(
    *,
    agent: Any,
    deps: SkillDeps,
    intent_plan: ToolIntentPlan | None,
    max_file_bytes: int,
) -> Optional[dict[str, Any]]:
    """Resolve the selected markdown skill for stage-two prompt expansion."""
    if intent_plan is None:
        return None

    capability_index = collect_capability_index_snapshot(agent=agent, deps=deps)
    if not capability_index:
        return None

    selected_capability_ids = {
        _normalize_text(item).lower()
        for item in selected_capability_ids_from_intent_plan(intent_plan)
        if _normalize_text(item)
    }
    target_provider_instances = {
        _normalize_text(item).lower()
        for item in (intent_plan.target_provider_instances or [])
        if _normalize_text(item)
    }
    target_provider_skill_names_ordered = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_provider_skill_names or [])
        if _normalize_text(item)
    ]
    target_provider_skill_names = set(target_provider_skill_names_ordered)
    target_skill_names_ordered = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_skill_names or [])
        if _normalize_text(item)
    ]
    target_skill_names = set(target_skill_names_ordered)
    target_tool_names_ordered = [
        _normalize_text(item).lower()
        for item in (intent_plan.target_tool_names or [])
        if _normalize_text(item)
    ]
    target_tool_names = set(target_tool_names_ordered)
    target_capability_classes = {
        _normalize_text(item).lower()
        for item in (intent_plan.target_capability_classes or [])
        if _normalize_text(item)
    }
    target_skill_order = {
        name: index
        for index, name in enumerate(target_skill_names_ordered)
    }
    target_provider_skill_order = {
        name: index
        for index, name in enumerate(target_provider_skill_names_ordered)
    }
    target_tool_order = {
        name: index
        for index, name in enumerate(target_tool_names_ordered)
    }

    matching_entries: list[tuple[tuple[int, int, int, int, int, int, int], dict[str, Any]]] = []
    for original_index, entry in enumerate(capability_index):
        if not isinstance(entry, dict):
            continue
        if _normalize_text(entry.get("kind", "")).lower() not in {"md_skill", "provider_skill"}:
            continue
        file_path = _normalize_text(entry.get("locator", ""))
        if not file_path:
            continue
        if not _match_selected_md_skill_entry(
            entry=entry,
            selected_capability_ids=selected_capability_ids,
            target_provider_instances=target_provider_instances,
            target_provider_skill_names=target_provider_skill_names,
            target_skill_names=target_skill_names,
            target_tool_names=target_tool_names,
            target_capability_classes=target_capability_classes,
        ):
            continue
        matching_entries.append(
            (
                _rank_selected_md_skill_entry(
                    entry=entry,
                    original_index=original_index,
                    selected_capability_ids=selected_capability_ids,
                    target_provider_skill_order=target_provider_skill_order,
                    target_skill_order=target_skill_order,
                    target_tool_order=target_tool_order,
                    target_capability_classes=target_capability_classes,
                ),
                entry,
            )
        )

    if not matching_entries:
        return None

    _, selected_entry = min(matching_entries, key=lambda item: item[0])
    file_path = _normalize_text(selected_entry.get("locator", ""))
    provider = _normalize_text(selected_entry.get("provider_type", ""))
    instructions = _load_target_md_skill_full_instructions(
        file_path=file_path,
    )
    return {
        "provider": provider,
        "provider_name": _normalize_text(selected_entry.get("provider_name", "")),
        "instance_name": _normalize_text(selected_entry.get("instance_name", "")),
        "provider_skill_name": _normalize_text(selected_entry.get("provider_skill_name", "")),
        "qualified_name": _normalize_text(
            selected_entry.get("qualified_skill_name") or selected_entry.get("name", "")
        ),
        "description": _normalize_text(selected_entry.get("description", "")),
        "artifact_types": list(selected_entry.get("artifact_types", []) or []),
        "file_path": file_path,
        "instructions": instructions,
    }


def enrich_target_md_skill_with_workflow_context(
    *,
    target_md_skill: Optional[dict[str, Any]],
    workflow_trace: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Attach current-turn workflow context to the selected markdown skill prompt."""
    if not isinstance(target_md_skill, dict):
        return target_md_skill
    enriched = dict(target_md_skill)
    if isinstance(workflow_trace, dict) and workflow_trace:
        enriched["workflow_context"] = dict(workflow_trace)
    return enriched


_EMBED_SCOPE_IDENTITY_KEYS = (
    "context_id",
    "generation",
    "provider_type",
    "provider_instance",
    "object_type",
    "object_id",
)


def _normalize_embed_scope_identity(value: Any) -> Optional[tuple[str, ...]]:
    """Return a stable identity for a server-restored embed context."""
    if not isinstance(value, dict):
        return None
    identity = tuple(
        "" if value.get(key) is None else str(value.get(key)).strip()
        for key in _EMBED_SCOPE_IDENTITY_KEYS
    )
    if not identity[0] or not identity[1]:
        return None
    return identity


def _embed_scope_workflow_history(
    message_history: list[dict[str, Any]],
    *,
    embed_scope: dict[str, Any],
) -> Optional[list[dict[str, Any]]]:
    """Return messages after the latest hidden action when its page identity still matches."""
    if not isinstance(message_history, list):
        return None
    current_identity = _normalize_embed_scope_identity(embed_scope)
    if current_identity is None:
        return None
    for index in range(len(message_history) - 1, -1, -1):
        message = message_history[index]
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").strip().lower() != "user":
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("visible_user_turn") is not False:
            continue
        if _normalize_embed_scope_identity(metadata.get("embed_scope")) != current_identity:
            return None
        return message_history[index + 1 :]
    return None


def _parse_target_md_skill_workflow_metadata(value: Any) -> Any:
    """Normalize runtime-only metadata into a compact prompt-safe structure."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return text
    return str(value)


def _infer_active_request_trace_id(
    recent_history: list[dict[str, Any]],
) -> Optional[str]:
    """Infer the active internal_request_trace_id from recent tool metadata.

    Scans message history in reverse to find the most recent tool result
    that carries an internal_request_trace_id in its _internal metadata.
    Returns the trace ID string or None if not found.
    """
    if not isinstance(recent_history, list):
        return None
    for message in reversed(recent_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").strip().lower() != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            continue
        internal = content.get("_internal")
        if internal is None:
            continue
        # _internal may be a JSON string or a dict/list
        if isinstance(internal, str):
            try:
                internal = json.loads(internal)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        # Could be a list of entries or a single dict
        if isinstance(internal, list):
            for item in reversed(internal):
                if isinstance(item, dict):
                    trace_id = item.get("internal_request_trace_id")
                    if isinstance(trace_id, str) and trace_id.strip():
                        return trace_id.strip()
        elif isinstance(internal, dict):
            trace_id = internal.get("internal_request_trace_id")
            if isinstance(trace_id, str) and trace_id.strip():
                return trace_id.strip()
    return None


def _extract_trace_id_from_metadata(metadata: Any) -> Optional[str]:
    """Extract internal_request_trace_id from a parsed metadata value."""
    if isinstance(metadata, dict):
        trace_id = metadata.get("internal_request_trace_id")
        if isinstance(trace_id, str) and trace_id.strip():
            return trace_id.strip()
    elif isinstance(metadata, list):
        for item in metadata:
            if isinstance(item, dict):
                trace_id = item.get("internal_request_trace_id")
                if isinstance(trace_id, str) and trace_id.strip():
                    return trace_id.strip()
    return None


def _extract_workflow_candidate_items_from_metadata(
    metadata: Any,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Return the candidate container key and candidate items when metadata is a single list payload."""
    if isinstance(metadata, list) and all(isinstance(item, dict) for item in metadata):
        return "__root__", [dict(item) for item in metadata]
    if not isinstance(metadata, dict):
        return None, []

    list_keys = [
        key
        for key, value in metadata.items()
        if isinstance(value, list) and all(isinstance(item, dict) for item in value)
    ]
    if len(list_keys) != 1:
        return None, []
    key = list_keys[0]
    return key, [dict(item) for item in metadata.get(key, [])]


def _workflow_candidate_selection_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("id", "entityId", "key", "code"):
        value = str(item.get(key) or "").strip()
        if value:
            tokens.add(value)
    return tokens


def _normalize_workflow_candidate_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def _workflow_candidate_mention_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("name", "nameZh", "label", "title", "displayName", "display_name"):
        token = _normalize_workflow_candidate_text(item.get(key))
        if len(token) >= 2 and not token.isdigit():
            tokens.add(token)
    return tokens


def _collect_explicit_selection_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, dict):
        for nested in value.values():
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, list):
        for nested in value:
            tokens.update(_collect_explicit_selection_tokens(nested))
        return tokens
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return tokens
        if text[:1] in {"{", "["}:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                tokens.update(_collect_explicit_selection_tokens(parsed))
                return tokens
        tokens.add(text)
        return tokens
    if isinstance(value, (int, float)):
        tokens.add(str(value))
        return tokens
    return tokens


def _narrow_target_md_skill_workflow_metadata(
    metadata: Any,
    *,
    following_messages: list[dict[str, Any]],
) -> Any:
    """Narrow candidate-list metadata to the explicitly selected item for active workflow context."""
    if not following_messages:
        return metadata

    container_key, candidates = _extract_workflow_candidate_items_from_metadata(metadata)
    if not container_key or len(candidates) <= 1:
        return metadata

    candidate_lookup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for token in _workflow_candidate_selection_tokens(candidate):
            candidate_lookup.setdefault(token, candidate)
    if not candidate_lookup:
        return metadata

    matched: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    mention_candidates = [
        (candidate, _workflow_candidate_mention_tokens(candidate)) for candidate in candidates
    ]
    for message in following_messages:
        if str(message.get("role", "")).strip().lower() != "assistant":
            continue
        for call in message.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            args = call.get("args", call.get("arguments"))
            for token in _collect_explicit_selection_tokens(args):
                candidate = candidate_lookup.get(token)
                if not candidate:
                    continue
                signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                matched.append(dict(candidate))
        normalized_content = _normalize_workflow_candidate_text(message.get("content"))
        if not normalized_content:
            continue
        for candidate, mention_tokens in mention_candidates:
            if not mention_tokens or not any(token in normalized_content for token in mention_tokens):
                continue
            signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            matched.append(dict(candidate))

    if len(matched) != 1:
        return metadata
    if container_key == "__root__":
        return matched
    if not isinstance(metadata, dict):
        return metadata

    narrowed = dict(metadata)
    narrowed[container_key] = matched
    return narrowed


def _collect_same_flow_following_messages(
    *,
    recent_history: list[dict[str, Any]],
    start_index: int,
    entry_trace_id: Optional[str],
) -> list[dict[str, Any]]:
    if not isinstance(recent_history, list):
        return []
    following_messages: list[dict[str, Any]] = []
    for message in recent_history[start_index + 1 :]:
        if entry_trace_id and isinstance(message, dict):
            if str(message.get("role", "") or "").strip().lower() == "tool":
                content = message.get("content")
                if isinstance(content, dict) and "_internal" in content:
                    parsed_metadata = _parse_target_md_skill_workflow_metadata(content.get("_internal"))
                    following_trace_id = _extract_trace_id_from_metadata(parsed_metadata)
                    if following_trace_id and following_trace_id != entry_trace_id:
                        break
        following_messages.append(message)
    return following_messages


def build_target_md_skill_workflow_context(
    *,
    recent_history: list[dict[str, Any]],
    active_trace_id: Optional[str] = None,
    max_entries: int = 6,
    max_chars: int = 12000,
) -> Optional[dict[str, Any]]:
    """Collect recent tool metadata for the current selected markdown skill only.

    When an active_trace_id is provided (or inferred from recent history),
    only metadata entries belonging to the same trace are collected.  This
    ensures that multiple request flow instances within the same session do
    not cross-contaminate each other's workflow context.

    If no trace ID is available (legacy providers), falls back to collecting
    all recent _internal metadata (backward compatible).
    """
    if not isinstance(recent_history, list) or not recent_history:
        return None

    # Determine the active trace ID
    resolved_trace_id: Optional[str] = None
    if isinstance(active_trace_id, str) and active_trace_id.strip():
        resolved_trace_id = active_trace_id.strip()
    else:
        resolved_trace_id = _infer_active_request_trace_id(recent_history)

    safe_max_entries = max(1, int(max_entries or 0))
    safe_max_chars = max(512, int(max_chars or 0))
    same_trace_metadata: list[dict[str, Any]] = []
    same_trace_size = 0
    legacy_metadata: list[dict[str, Any]] = []
    legacy_size = 0

    for message_index in range(len(recent_history) - 1, -1, -1):
        message = recent_history[message_index]
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").strip().lower() != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            continue
        if "_internal" not in content:
            continue

        metadata = _parse_target_md_skill_workflow_metadata(content.get("_internal"))
        if metadata is None:
            continue

        # Filter by trace ID if one is active
        if resolved_trace_id:
            entry_trace_id = _extract_trace_id_from_metadata(metadata)
            if entry_trace_id and entry_trace_id != resolved_trace_id:
                # Belongs to a different request flow instance — skip
                continue

        entry_trace_id = _extract_trace_id_from_metadata(metadata)
        metadata = _narrow_target_md_skill_workflow_metadata(
            metadata,
            following_messages=_collect_same_flow_following_messages(
                recent_history=recent_history,
                start_index=message_index,
                entry_trace_id=entry_trace_id,
            ),
        )

        entry = {
            "tool_name": str(message.get("tool_name", "") or message.get("name", "")).strip(),
            "metadata": metadata,
        }
        serialized_entry = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        if len(serialized_entry) > safe_max_chars:
            continue
        if resolved_trace_id:
            entry_trace_id = _extract_trace_id_from_metadata(metadata)
            if entry_trace_id == resolved_trace_id:
                if same_trace_metadata and same_trace_size + len(serialized_entry) > safe_max_chars:
                    break
                same_trace_metadata.append(entry)
                same_trace_size += len(serialized_entry)
                if len(same_trace_metadata) >= safe_max_entries:
                    break
                continue
            if entry_trace_id:
                continue
            if legacy_metadata and legacy_size + len(serialized_entry) > safe_max_chars:
                continue
            if len(legacy_metadata) >= safe_max_entries:
                continue
            legacy_metadata.append(entry)
            legacy_size += len(serialized_entry)
            continue
        if legacy_metadata and legacy_size + len(serialized_entry) > safe_max_chars:
            break
        legacy_metadata.append(entry)
        legacy_size += len(serialized_entry)
        if len(legacy_metadata) >= safe_max_entries:
            break

    recent_tool_metadata = same_trace_metadata if same_trace_metadata else legacy_metadata
    if not recent_tool_metadata:
        return None

    recent_tool_metadata.reverse()
    result: dict[str, Any] = {"recent_tool_metadata": recent_tool_metadata}
    if resolved_trace_id:
        result["internal_request_trace_id"] = resolved_trace_id
    return result


def prune_auto_selected_provider_instance_tools(
    *,
    available_tools: list[dict[str, Any]],
    deps: Optional[SkillDeps],
    intent_plan: ToolIntentPlan | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove provider-selector tools once the provider instance is fixed."""
    trace: dict[str, Any] = {
        "enabled": False,
        "removed_tools": [],
        "target_provider_instances": [],
        "target_provider_types": [],
        "auto_selected_provider_types": [],
        "explicit_selected_provider_types": [],
        "explicit_selected_instances": [],
    }
    if not available_tools:
        return list(available_tools or []), trace
    if deps is None or not isinstance(getattr(deps, "extra", None), dict):
        return list(available_tools), trace

    extra = deps.extra
    provider_instances = extra.get("provider_instances")
    if not isinstance(provider_instances, dict) or not provider_instances:
        return list(available_tools), trace

    explicit_selected_provider_types: list[str] = []
    explicit_selected_instances: list[str] = []
    target_provider_instances: list[str] = []
    selected_capability = extra.get(SELECTED_CAPABILITY_KEY)
    if isinstance(selected_capability, dict):
        selected_provider_type, selected_instance_name = selected_capability_provider_instance_ref(
            selected_capability
        )
        selected_provider_type = selected_provider_type.lower()
        if selected_provider_type and selected_instance_name:
            explicit_selected_provider_types.append(selected_provider_type)
            explicit_selected_instances.append(selected_instance_name)

    target_provider_types: list[str] = []
    if intent_plan is not None:
        for item in (intent_plan.target_provider_instances or []):
            provider_type, instance_name = _split_provider_instance_ref(item)
            provider_type = provider_type.strip().lower()
            if not provider_type or not instance_name:
                continue
            target_provider_instances.append(f"{provider_type}.{instance_name}")
            if provider_type not in target_provider_types:
                target_provider_types.append(provider_type)
            if provider_type not in explicit_selected_provider_types:
                explicit_selected_provider_types.append(provider_type)
                explicit_selected_instances.append(instance_name)
        for item in (intent_plan.target_provider_types or []):
            provider_type = str(item or "").strip().lower()
            if provider_type and provider_type not in target_provider_types:
                target_provider_types.append(provider_type)

    for provider_type in explicit_selected_provider_types:
        if provider_type not in target_provider_types:
            target_provider_types.append(provider_type)

    if not target_provider_types:
        selected_provider_type = ""
        provider_instance = extra.get("provider_instance")
        if isinstance(provider_instance, dict):
            selected_provider_type = str(
                provider_instance.get("provider_type", "") or ""
            ).strip().lower()
        if not selected_provider_type:
            selected_provider_type = str(extra.get("provider_type", "") or "").strip().lower()
        if selected_provider_type:
            target_provider_types.append(selected_provider_type)
        selected_instance_name = str(extra.get("provider_instance_name", "") or "").strip()
        if selected_provider_type and selected_instance_name:
            explicit_selected_provider_types.append(selected_provider_type)
            explicit_selected_instances.append(selected_instance_name)

    auto_selected_provider_types = [
        provider_type
        for provider_type in target_provider_types
        if (
            isinstance(provider_instances.get(provider_type), dict)
            and len(provider_instances.get(provider_type) or {}) == 1
        )
    ]
    prune_provider_types = set(auto_selected_provider_types)
    prune_provider_types.update(explicit_selected_provider_types)
    if not prune_provider_types:
        return list(available_tools), trace

    filtered_tools: list[dict[str, Any]] = []
    removed_tools: list[str] = []
    for tool in available_tools:
        if not isinstance(tool, dict):
            continue
        normalized_group_ids = {
            str(group_id or "").strip().lower()
            for group_id in (tool.get("group_ids", []) or [])
            if str(group_id or "").strip()
        }
        capability_class = str(tool.get("capability_class", "") or "").strip().lower()
        is_provider_selector = bool(tool.get("coordination_only")) and (
            "group:providers" in normalized_group_ids or capability_class == "provider:generic"
        )
        tool_name = str(tool.get("name", "") or "").strip()
        if is_provider_selector:
            removed_tools.append(tool_name or "<unnamed>")
            continue
        filtered_tools.append(dict(tool))

    trace.update(
        {
            "enabled": bool(removed_tools),
            "removed_tools": removed_tools,
            "target_provider_instances": target_provider_instances,
            "target_provider_types": list(target_provider_types),
            "auto_selected_provider_types": auto_selected_provider_types,
            "explicit_selected_provider_types": explicit_selected_provider_types,
            "explicit_selected_instances": explicit_selected_instances,
        }
    )
    if not removed_tools:
        return list(available_tools), trace
    return filtered_tools, trace
