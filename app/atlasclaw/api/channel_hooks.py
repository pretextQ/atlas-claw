# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Channel webhook routes for receiving messages from external platforms."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.atlasclaw.api.channels import get_channel_manager
from app.atlasclaw.channels.registry import ChannelRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-hooks", tags=["channel-hooks"])


@router.post("/{channel_type}/{connection_id}")
async def receive_channel_webhook(
    channel_type: str,
    connection_id: str,
    request: Request
) -> JSONResponse:
    """Receive a webhook event from an external channel platform.

    The raw platform payload is handed to the connection's handler for
    parsing; the resulting inbound message is then scheduled onto the Agent
    runner through the ChannelManager (same path as long-connection traffic).

    Args:
        channel_type: Channel type (e.g., feishu, dingtalk)
        connection_id: Connection identifier
        request: FastAPI request object

    Returns:
        JSON response
    """
    handler_class = ChannelRegistry.get(channel_type)
    if not handler_class:
        logger.error(f"Channel type not found: {channel_type}")
        raise HTTPException(status_code=404, detail=f"Channel type not found: {channel_type}")

    manager = get_channel_manager()
    found = manager.find_active_connection(channel_type, connection_id)
    if not found:
        logger.warning(f"No active handler for webhook: {channel_type}/{connection_id}")
        raise HTTPException(
            status_code=404,
            detail=f"Connection not found or not active: {connection_id}",
        )
    user_id, handler = found

    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            raw_event: Any = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid JSON body")
    else:
        # Handlers accept JSON strings and parse them themselves.
        raw_event = raw_body.decode("utf-8", errors="replace")

    try:
        inbound = await handler.handle_inbound(raw_event)
    except Exception as e:
        logger.error(f"Failed to parse inbound message from {channel_type}: {e}")
        raise HTTPException(status_code=400, detail="Invalid message format")
    if not inbound:
        logger.warning(f"Handler could not parse inbound message from {channel_type}")
        raise HTTPException(status_code=400, detail="Invalid message format")

    manager.schedule_inbound_processing(user_id, channel_type, connection_id, inbound)

    return JSONResponse(content={"status": "ok", "message_id": inbound.message_id})


@router.get("/{channel_type}/{connection_id}")
async def verify_channel_webhook(
    channel_type: str,
    connection_id: str,
    request: Request
) -> JSONResponse:
    """Verify webhook endpoint (for platforms that require verification).

    Args:
        channel_type: Channel type
        connection_id: Connection identifier
        request: FastAPI request object

    Returns:
        JSON response
    """
    try:
        # Some platforms (like Feishu) require challenge verification
        params = dict(request.query_params)

        if "challenge" in params:
            # Return challenge for verification
            return JSONResponse(content={"challenge": params["challenge"]})

        return JSONResponse(content={"status": "ok"})

    except Exception as e:
        logger.error(f"Failed to verify webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
