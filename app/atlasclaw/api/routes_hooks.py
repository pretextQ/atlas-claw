# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.models import ANONYMOUS_USER, UserInfo
from .deps_context import APIContext, get_api_context
from .schemas import (
    HookDecisionRequest,
    HookDecisionResponse,
    HookEventResponse,
    HookPendingResponse,
)


def _current_user(request_obj: Request) -> UserInfo:
    return getattr(request_obj.state, "user_info", ANONYMOUS_USER)


def register_hook_routes(router: APIRouter) -> None:
    @router.get("/hooks/{module}/events", response_model=list[HookEventResponse])
    async def list_hook_events(
        module: str,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> list[HookEventResponse]:
        auth_user = _current_user(request_obj)
        if ctx.hook_state_store is None:
            return []
        events = await ctx.hook_state_store.list_events(module, auth_user.user_id)
        return [
            HookEventResponse(
                id=item.id,
                event_type=item.event_type.value,
                user_id=item.user_id,
                session_key=item.session_key,
                run_id=item.run_id,
                channel=item.channel,
                agent_id=item.agent_id,
                created_at=item.created_at,
                payload=item.payload,
            )
            for item in events
        ]

    @router.get("/hooks/{module}/pending", response_model=list[HookPendingResponse])
    async def list_hook_pending(
        module: str,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> list[HookPendingResponse]:
        auth_user = _current_user(request_obj)
        if ctx.hook_state_store is None:
            return []
        pending = await ctx.hook_state_store.list_pending(module, auth_user.user_id)
        return [
            HookPendingResponse(
                id=item.id,
                module_name=item.module_name,
                user_id=item.user_id,
                source_event_ids=item.source_event_ids,
                summary=item.summary,
                payload=item.payload,
                status=item.status.value,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in pending
        ]

    @router.post("/hooks/{module}/pending/{pending_id}/confirm", response_model=HookDecisionResponse)
    async def confirm_hook_pending(
        module: str,
        pending_id: str,
        request: HookDecisionRequest,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> HookDecisionResponse:
        auth_user = _current_user(request_obj)
        if ctx.hook_runtime is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hook runtime not initialized",
            )
        resolved = await ctx.hook_runtime.resolve_pending(
            module_name=module,
            user_id=auth_user.user_id,
            pending_id=pending_id,
            decision="confirm",
            decided_by=auth_user.user_id,
            note=request.note or "",
        )
        return HookDecisionResponse(
            pending_id=resolved.id,
            module_name=module,
            decision="confirm",
            status=resolved.status.value,
        )

    @router.post("/hooks/{module}/pending/{pending_id}/reject", response_model=HookDecisionResponse)
    async def reject_hook_pending(
        module: str,
        pending_id: str,
        request: HookDecisionRequest,
        request_obj: Request,
        ctx: APIContext = Depends(get_api_context),
    ) -> HookDecisionResponse:
        auth_user = _current_user(request_obj)
        if ctx.hook_runtime is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hook runtime not initialized",
            )
        resolved = await ctx.hook_runtime.resolve_pending(
            module_name=module,
            user_id=auth_user.user_id,
            pending_id=pending_id,
            decision="reject",
            decided_by=auth_user.user_id,
            note=request.note or "",
        )
        return HookDecisionResponse(
            pending_id=resolved.id,
            module_name=module,
            decision="reject",
            status=resolved.status.value,
        )
