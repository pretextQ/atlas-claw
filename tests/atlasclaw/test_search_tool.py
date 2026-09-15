# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from types import SimpleNamespace

import pytest

from app.atlasclaw.tools.web.provider_runtime import SearchExecutionResponse
from app.atlasclaw.tools.web.search_tool import web_search_tool


@pytest.mark.asyncio
async def test_web_search_tool_uses_runtime_from_project_config(monkeypatch) -> None:
    observed = {}

    class _FakeRuntime:
        async def execute(self, *, query: str, provider_override: str | None, limit: int, require_grounding: bool = False):
            observed["query"] = query
            observed["provider_override"] = provider_override
            observed["limit"] = limit
            return SearchExecutionResponse(
                provider="google_html_fallback",
                query=query,
                results=[],
                expanded_queries=[query],
            )

    def fake_get_config():
        return SimpleNamespace(search_runtime=SimpleNamespace(default_provider='google_html_fallback'))

    def fake_from_config(config):
        observed["config_default_provider"] = config.default_provider
        return _FakeRuntime()

    monkeypatch.setattr('app.atlasclaw.tools.web.search_tool.get_config', fake_get_config)
    monkeypatch.setattr(
        'app.atlasclaw.tools.web.search_tool.SearchExecutionRuntime.from_config',
        fake_from_config,
    )

    result = await web_search_tool(ctx=SimpleNamespace(), query='OpenClaw', provider=None, limit=5)

    assert observed["config_default_provider"] == 'google_html_fallback'
    assert observed["provider_override"] is None
    assert result["details"]["provider"] == 'google_html_fallback'


@pytest.mark.asyncio
async def test_web_search_tool_passes_provider_override(monkeypatch) -> None:
    observed = {}

    class _FakeRuntime:
        async def execute(self, *, query: str, provider_override: str | None, limit: int, require_grounding: bool = False):
            observed["query"] = query
            observed["provider_override"] = provider_override
            observed["limit"] = limit
            return SearchExecutionResponse(
                provider=provider_override or "bing_html_fallback",
                query=query,
                results=[],
            )

    monkeypatch.setattr('app.atlasclaw.tools.web.search_tool.get_config', lambda: SimpleNamespace(search_runtime=SimpleNamespace(default_provider='bing_html_fallback')))
    monkeypatch.setattr(
        'app.atlasclaw.tools.web.search_tool.SearchExecutionRuntime.from_config',
        lambda _config: _FakeRuntime(),
    )

    result = await web_search_tool(
        ctx=SimpleNamespace(),
        query='上海明天天气',
        provider='google_html_fallback',
        limit=3,
    )

    assert observed["provider_override"] == 'google_html_fallback'
    assert observed["limit"] == 3
    assert result["details"]["provider"] == 'google_html_fallback'


@pytest.mark.asyncio
async def test_web_search_tool_returns_error_when_runtime_fails(monkeypatch) -> None:
    class _FailingRuntime:
        async def execute(self, *, query: str, provider_override: str | None, limit: int, require_grounding: bool = False):
            raise RuntimeError("network unreachable")

    monkeypatch.setattr('app.atlasclaw.tools.web.search_tool.get_config', lambda: SimpleNamespace(search_runtime=SimpleNamespace(default_provider='bing_html_fallback')))
    monkeypatch.setattr(
        'app.atlasclaw.tools.web.search_tool.SearchExecutionRuntime.from_config',
        lambda _config: _FailingRuntime(),
    )

    result = await web_search_tool(ctx=SimpleNamespace(), query='上海明天天气', provider=None, limit=5)

    assert result["is_error"] is True
    assert "Search runtime failed" in result["content"][0]["text"]
    assert result["details"]["query"] == '上海明天天气'
