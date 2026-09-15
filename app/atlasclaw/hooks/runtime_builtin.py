# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.atlasclaw.hooks.runtime import HookHandlerDefinition, HookRuntime
from app.atlasclaw.hooks.runtime_models import HookEventType, PendingHookItem


RUNTIME_AUDIT_MODULE = "runtime-audit"
PENDING_EVENT_TYPES = {
    HookEventType.MESSAGE_USER_CORRECTED,
    HookEventType.RUN_FAILED,
    HookEventType.LLM_FAILED,
    HookEventType.TOOL_FAILED,
}


def register_builtin_hook_handlers(runtime: HookRuntime) -> None:
    """Register generic built-in handlers for runtime observability."""

    async def runtime_audit_handler(event, runtime_context):
        await runtime_context.hook_state_store.append_event(RUNTIME_AUDIT_MODULE, event)

        if event.event_type not in PENDING_EVENT_TYPES:
            return

        summary = _build_pending_summary(event)
        now = datetime.now(timezone.utc)
        await runtime_context.hook_state_store.append_pending(
            RUNTIME_AUDIT_MODULE,
            PendingHookItem(
                id=f"pending-{uuid4().hex}",
                module_name=RUNTIME_AUDIT_MODULE,
                user_id=event.user_id,
                source_event_ids=[event.id],
                summary=summary,
                payload={
                    "body": summary,
                    "event_type": event.event_type.value,
                    "payload": dict(event.payload),
                },
                created_at=now,
                updated_at=now,
            ),
        )

    runtime.register(
        HookHandlerDefinition(
            module_name=RUNTIME_AUDIT_MODULE,
            event_types=set(HookEventType),
            handler=runtime_audit_handler,
            priority=100,
        )
    )


def _build_pending_summary(event) -> str:
    if event.event_type == HookEventType.MESSAGE_USER_CORRECTED:
        correction = str(event.payload.get("correction") or event.payload.get("message") or "").strip()
        return correction or "User provided a correction that may need review"
    if event.event_type == HookEventType.RUN_FAILED:
        return str(event.payload.get("error") or "Agent run failed")
    if event.event_type == HookEventType.LLM_FAILED:
        return str(event.payload.get("error") or "LLM request failed")
    if event.event_type == HookEventType.TOOL_FAILED:
        tool_name = str(event.payload.get("tool_name") or event.payload.get("tool") or "unknown_tool")
        error = str(event.payload.get("error") or "Tool execution failed")
        return f"{tool_name}: {error}"
    return f"Review hook event: {event.event_type.value}"
