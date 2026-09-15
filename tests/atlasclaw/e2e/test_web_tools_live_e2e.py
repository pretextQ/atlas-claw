# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import os
import re
import time

import pytest

from app.atlasclaw.tools.web.fetch_tool import fetch_web_content
from app.atlasclaw.tools.web.provider_runtime import build_default_search_runtime


async def _fetch_first_non_empty_result(
    *,
    urls: list[str],
    timeout_seconds: float,
) -> tuple[str, dict[str, object], str]:
    for candidate_url in urls:
        try:
            content, details = await fetch_web_content(
                candidate_url,
                extract_mode="text",
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            continue
        if details.get("status_code") == 200 and content.strip():
            return content, details, candidate_url
    return "", {}, ""


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_web_pipeline_weather_query_timing_breakdown() -> None:
    if os.getenv("ATLASCLAW_LIVE_E2E", "").strip() != "1":
        pytest.skip("Set ATLASCLAW_LIVE_E2E=1 to run live network web pipeline E2E.")

    query = "上海周日天气"
    runtime = build_default_search_runtime()

    t0 = time.perf_counter()
    search_start = time.perf_counter()
    response = await runtime.execute(
        query=query,
        provider_override=None,
        limit=5,
        require_grounding=True,
        overall_timeout_seconds=10.0,
    )
    search_elapsed_ms = round((time.perf_counter() - search_start) * 1000, 1)

    assert response.results, "live search returned no results"
    merged_search_text = " ".join(
        " ".join((item.title or "", item.snippet or ""))
        for item in response.results[:5]
    )
    assert re.search(r"(天气|预报|weather|forecast)", merged_search_text, flags=re.IGNORECASE), (
        "weather query should return weather-related search evidence"
    )
    urls = [item.url for item in response.results[:5] if item.url]

    fetch_start = time.perf_counter()
    content, details, used_url = await _fetch_first_non_empty_result(
        urls=urls,
        timeout_seconds=10.0,
    )
    fetch_elapsed_ms = round((time.perf_counter() - fetch_start) * 1000, 1)
    total_elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    assert used_url, "weather query should have at least one fetchable result URL"
    assert details.get("status_code") == 200
    assert content.strip()
    normalized = " ".join(content.split())
    assert re.search(r"\d", normalized), "fetched weather content should contain concrete values"

    print(
        "LIVE_E2E_TIMING "
        f"query={query} provider={response.provider} "
        f"search_ms={search_elapsed_ms} fetch_ms={fetch_elapsed_ms} total_ms={total_elapsed_ms} "
        f"result_count={len(response.results)} url={used_url}"
    )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_web_pipeline_hiking_query_returns_route_evidence() -> None:
    if os.getenv("ATLASCLAW_LIVE_E2E", "").strip() != "1":
        pytest.skip("Set ATLASCLAW_LIVE_E2E=1 to run live network web pipeline E2E.")

    query = "苏州徒步推荐"
    runtime = build_default_search_runtime()

    t0 = time.perf_counter()
    search_start = time.perf_counter()
    response = await runtime.execute(
        query=query,
        provider_override=None,
        limit=5,
        require_grounding=True,
        overall_timeout_seconds=12.0,
    )
    search_elapsed_ms = round((time.perf_counter() - search_start) * 1000, 1)

    assert response.results, "live search returned no results for hiking query"
    merged_text = " ".join(
        " ".join((item.title or "", item.snippet or ""))
        for item in response.results[:5]
    )
    assert re.search(r"(徒步|路线|登山|trail|hiking)", merged_text, flags=re.IGNORECASE), (
        "hiking query should return route-related evidence"
    )

    urls = [item.url for item in response.results[:5] if item.url]
    fetch_start = time.perf_counter()
    content, details, used_url = await _fetch_first_non_empty_result(
        urls=urls,
        timeout_seconds=12.0,
    )
    fetch_elapsed_ms = round((time.perf_counter() - fetch_start) * 1000, 1)
    total_elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    assert used_url, "hiking query should have at least one fetchable result URL"
    assert details.get("status_code") == 200
    assert content.strip()
    normalized = " ".join(content.split())
    assert len(normalized) >= 120, "fetched route content is unexpectedly short"

    print(
        "LIVE_E2E_TIMING "
        f"query={query} provider={response.provider} "
        f"search_ms={search_elapsed_ms} fetch_ms={fetch_elapsed_ms} total_ms={total_elapsed_ms} "
        f"result_count={len(response.results)} url={used_url}"
    )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_web_pipeline_compact_weather_query_returns_weather_evidence() -> None:
    if os.getenv("ATLASCLAW_LIVE_E2E", "").strip() != "1":
        pytest.skip("Set ATLASCLAW_LIVE_E2E=1 to run live network web pipeline E2E.")

    query = "周末上海天气"

    runtime = build_default_search_runtime()
    response = await runtime.execute(
        query=query,
        provider_override=None,
        limit=5,
        require_grounding=True,
        overall_timeout_seconds=10.0,
    )

    assert response.results, "compact weather query returned no results"
    merged_search_text = " ".join(
        " ".join((item.title or "", item.snippet or ""))
        for item in response.results[:5]
    )
    assert re.search(r"(天气|预报|weather|forecast)", merged_search_text, flags=re.IGNORECASE)

    urls = [item.url for item in response.results[:5] if item.url]
    content, details, used_url = await _fetch_first_non_empty_result(
        urls=urls,
        timeout_seconds=10.0,
    )
    assert used_url, "compact weather query should have at least one fetchable result URL"
    assert details.get("status_code") == 200
    normalized = " ".join(content.split())
    assert re.search(r"\d", normalized), "weather fetch should contain concrete values"


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_web_pipeline_compact_hiking_query_returns_route_evidence() -> None:
    if os.getenv("ATLASCLAW_LIVE_E2E", "").strip() != "1":
        pytest.skip("Set ATLASCLAW_LIVE_E2E=1 to run live network web pipeline E2E.")

    query = "上海周边徒步"

    runtime = build_default_search_runtime()
    response = await runtime.execute(
        query=query,
        provider_override=None,
        limit=5,
        require_grounding=True,
        overall_timeout_seconds=12.0,
    )

    assert response.results, "compact hiking query returned no results"
    merged_text = " ".join(
        " ".join((item.title or "", item.snippet or ""))
        for item in response.results[:5]
    )
    assert re.search(r"(徒步|路线|登山|trail|hiking)", merged_text, flags=re.IGNORECASE)

    urls = [item.url for item in response.results[:5] if item.url]
    content, details, used_url = await _fetch_first_non_empty_result(
        urls=urls,
        timeout_seconds=12.0,
    )
    assert used_url, "compact hiking query should have at least one fetchable result URL"
    assert details.get("status_code") == 200
    normalized = " ".join(content.split())
    assert len(normalized) >= 120, "fetched route content is unexpectedly short"
