# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
web_search tool

Provider-driven web search runtime. HTML scraping engines remain available only
as fallback adapters registered through the runtime.
"""

from __future__ import annotations

import inspect
from typing import Optional, TYPE_CHECKING

from app.atlasclaw.core.config import get_config
from app.atlasclaw.tools.base import ToolResult
from app.atlasclaw.tools.web.provider_runtime import SearchExecutionRuntime

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


async def web_search_tool(
    ctx: "RunContext[SkillDeps]",
    query: str,
    provider: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """Execute web search through the provider-driven runtime."""

    _ = ctx
    config = get_config()
    runtime_config = config.search_runtime
    runtime = SearchExecutionRuntime.from_config(runtime_config)
    try:
        execute_kwargs = {
            "query": query,
            "provider_override": provider,
            "limit": limit,
        }
        try:
            signature = inspect.signature(runtime.execute)
            if "overall_timeout_seconds" in signature.parameters:
                execute_kwargs["overall_timeout_seconds"] = getattr(
                    runtime_config,
                    "overall_timeout_seconds",
                    8.0,
                )
        except (TypeError, ValueError):
            pass
        response = await runtime.execute(**execute_kwargs)
    except Exception as exc:
        return ToolResult.error(
            f"Search runtime failed: {type(exc).__name__}: {exc}",
            details={
                "provider": provider or "bing_html_fallback",
                "query": query,
                "count": 0,
            },
        ).to_dict()

    return ToolResult.text(
        response.render_markdown(),
        details=response.model_dump(mode="json"),
    ).to_dict()
