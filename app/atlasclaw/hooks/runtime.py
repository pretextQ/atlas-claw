# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from app.atlasclaw.hooks.runtime_models import (
    HookDecision,
    HookEventType,
    HookEventEnvelope,
    HookScriptActionType,
    HookWriteMemoryRequest,
    PendingHookItem,
)
from app.atlasclaw.hooks.runtime_script import (
    HookScriptExecutionError,
    HookScriptHandlerDefinition,
    HookScriptRunner,
)
from app.atlasclaw.hooks.runtime_sinks import ContextSink, MemorySink
from app.atlasclaw.hooks.runtime_store import HookStateStore


HookHandler = Callable[[HookEventEnvelope, "HookRuntimeContext"], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass
class HookRuntimeContext:
    """Shared runtime context made available to hook handlers."""

    workspace_path: str
    hook_state_store: HookStateStore
    memory_sink: MemorySink
    context_sink: ContextSink
    session_manager_router: Optional[Any] = None
    memory_manager: Optional[Any] = None
    deps: Optional[Any] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookHandlerDefinition:
    """Definition for a hook runtime handler."""

    module_name: str
    event_types: set[HookEventType]
    handler: HookHandler
    priority: int = 100
    enabled: bool = True


class HookRuntime:
    """Generic hook runtime with registration, dispatch, and pending resolution."""

    def __init__(self, context: HookRuntimeContext):
        self.context = context
        self.state = context.hook_state_store
        self.memory = context.memory_sink
        self.context_sink = context.context_sink
        self._handlers: dict[HookEventType, list[HookHandlerDefinition]] = {
            event_type: [] for event_type in HookEventType
        }

    @property
    def context_store(self) -> ContextSink:
        """Backward-friendly alias for the context sink."""
        return self.context_sink

    @property
    def context(self) -> HookRuntimeContext:  # type: ignore[override]
        return self._context

    @context.setter
    def context(self, value: HookRuntimeContext) -> None:
        self._context = value

    def register(self, definition: HookHandlerDefinition) -> None:
        """Register a handler for one or more hook event types."""
        for event_type in definition.event_types:
            self._handlers[event_type].append(definition)
            self._handlers[event_type].sort(key=lambda item: item.priority)

    def register_script_handler(self, definition: HookScriptHandlerDefinition) -> None:
        """Register a script-backed hook handler."""
        runner = HookScriptRunner()

        async def _script_handler(event: HookEventEnvelope, runtime_context: HookRuntimeContext) -> None:
            await runtime_context.hook_state_store.append_event(definition.module_name, event)
            try:
                action_batch = await runner.run(definition, event)
                await self.apply_actions(
                    module_name=definition.module_name,
                    user_id=event.user_id,
                    source_event_ids=[event.id],
                    action_batch=action_batch,
                )
            except HookScriptExecutionError as exc:
                logger.warning("Script hook '%s' failed: %s", definition.module_name, exc)

        self.register(
            HookHandlerDefinition(
                module_name=definition.module_name,
                event_types=set(definition.event_types),
                handler=_script_handler,
                priority=definition.priority,
                enabled=definition.enabled,
            )
        )

    def list_handlers(self, event_type: Optional[HookEventType] = None) -> list[HookHandlerDefinition]:
        """Return registered handlers, optionally filtered by event type."""
        if event_type is not None:
            return list(self._handlers.get(event_type, []))
        seen: dict[tuple[str, int], HookHandlerDefinition] = {}
        for definitions in self._handlers.values():
            for definition in definitions:
                seen[(definition.module_name, definition.priority)] = definition
        return sorted(seen.values(), key=lambda item: item.priority)

    async def dispatch(self, event: HookEventEnvelope) -> None:
        """Dispatch a runtime event to all interested handlers."""
        for definition in self._handlers.get(event.event_type, []):
            if not definition.enabled:
                continue
            await definition.handler(event, self.context)

    async def emit(
        self,
        *,
        event_type: HookEventType,
        user_id: str,
        session_key: str,
        run_id: str,
        channel: str,
        agent_id: str,
        payload: Optional[dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> HookEventEnvelope:
        """Create an event envelope and dispatch it immediately."""
        envelope = HookEventEnvelope(
            id=event_id or f"hook-{uuid4().hex}",
            event_type=event_type,
            user_id=user_id,
            session_key=session_key,
            run_id=run_id,
            channel=channel,
            agent_id=agent_id,
            created_at=datetime.now(timezone.utc),
            payload=dict(payload or {}),
        )
        await self.dispatch(envelope)
        return envelope

    async def create_pending_item(
        self,
        *,
        module_name: str,
        user_id: str,
        source_event_ids: list[str],
        summary: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> PendingHookItem:
        """Append a generic pending item for later user confirmation."""
        now = datetime.now(timezone.utc)
        item = PendingHookItem(
            id=f"pending-{uuid4().hex}",
            module_name=module_name,
            user_id=user_id,
            source_event_ids=list(source_event_ids),
            summary=summary,
            payload=dict(payload or {}),
            created_at=now,
            updated_at=now,
        )
        await self.state.append_pending(module_name, item)
        return item

    async def apply_actions(
        self,
        *,
        module_name: str,
        user_id: str,
        source_event_ids: list[str],
        action_batch: Any,
    ) -> None:
        """Apply parsed structured actions returned by a script hook."""
        for action in getattr(action_batch, "actions", []):
            payload = dict(getattr(action, "payload", {}) or {})
            if action.action_type == HookScriptActionType.CREATE_PENDING:
                summary = str(payload.get("summary") or "").strip()
                if not summary:
                    raise ValueError("create_pending action requires 'summary'")
                body = str(payload.get("body") or summary).strip()
                metadata = dict(payload.get("metadata") or {})
                await self.create_pending_item(
                    module_name=module_name,
                    user_id=user_id,
                    source_event_ids=list(source_event_ids),
                    summary=summary,
                    payload={"body": body, **metadata},
                )
            elif action.action_type == HookScriptActionType.WRITE_MEMORY:
                title = str(payload.get("title") or "").strip()
                body = str(payload.get("body") or "").strip()
                if not body:
                    raise ValueError("write_memory action requires 'body'")
                metadata = dict(payload.get("metadata") or {})
                await self.memory.write_confirmed(
                    HookWriteMemoryRequest(
                        module_name=module_name,
                        user_id=user_id,
                        title=title or "Memory Entry",
                        body=body,
                        source_event_ids=list(source_event_ids),
                        metadata=metadata,
                    )
                )
                await self.emit(
                    event_type=HookEventType.MEMORY_CONFIRMED,
                    user_id=user_id,
                    session_key="",
                    run_id="",
                    channel="",
                    agent_id="",
                    payload={
                        "module_name": module_name,
                        "source_event_ids": list(source_event_ids),
                        "title": title or "Memory Entry",
                    },
                )
            elif action.action_type == HookScriptActionType.ADD_CONTEXT:
                summary = str(payload.get("summary") or "").strip()
                if not summary:
                    raise ValueError("add_context action requires 'summary'")
                body = str(payload.get("body") or "").strip()
                metadata = dict(payload.get("metadata") or {})
                context_payload = dict(metadata)
                if body:
                    context_payload["body"] = body
                await self.context_sink.add_injection(
                    module_name=module_name,
                    user_id=user_id,
                    summary=summary,
                    payload=context_payload,
                    source_event_ids=list(source_event_ids),
                )
            else:  # pragma: no cover
                raise ValueError(f"Unsupported hook action type: {action.action_type}")

    async def resolve_pending(
        self,
        *,
        module_name: str,
        user_id: str,
        pending_id: str,
        decision: str,
        decided_by: str,
        note: str = "",
    ) -> PendingHookItem:
        """Resolve a pending item and optionally promote it into long-term memory."""
        resolved = await self.state.resolve_pending(
            module_name=module_name,
            user_id=user_id,
            pending_id=pending_id,
            decision=decision,
            decided_by=decided_by,
            note=note,
        )
        if HookDecision(decision) == HookDecision.CONFIRM:
            await self.memory.write_confirmed(
                HookWriteMemoryRequest(
                    module_name=module_name,
                    user_id=user_id,
                    title=resolved.summary,
                    body=str(resolved.payload.get("body") or resolved.summary),
                    source_event_ids=list(resolved.source_event_ids),
                    metadata={"pending_id": resolved.id, "decision_note": note},
                )
            )
            await self.emit(
                event_type=HookEventType.MEMORY_CONFIRMED,
                user_id=user_id,
                session_key="",
                run_id="",
                channel="",
                agent_id="",
                payload={
                    "module_name": module_name,
                    "pending_id": resolved.id,
                    "decision_note": note,
                },
            )
        else:
            await self.emit(
                event_type=HookEventType.MEMORY_REJECTED,
                user_id=user_id,
                session_key="",
                run_id="",
                channel="",
                agent_id="",
                payload={
                    "module_name": module_name,
                    "pending_id": resolved.id,
                    "decision_note": note,
                },
            )
        return resolved
