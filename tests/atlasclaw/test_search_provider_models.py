# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from app.atlasclaw.core.config_schema import SearchRuntimeConfig
from app.atlasclaw.tools.web.provider_models import (
    GroundedSearchResponse,
    SearchProviderCapabilities,
    SearchProviderType,
    SourceTier,
)


def test_search_runtime_config_has_provider_registry_defaults() -> None:
    config = SearchRuntimeConfig()
    assert config.default_provider == 'bing_html_fallback'
    assert config.max_query_attempts == 3
    assert config.prefer_grounding is True


def test_search_runtime_config_uses_trust_env_for_proxy_resolution(monkeypatch) -> None:
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:10792')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:10792')
    config = SearchRuntimeConfig()
    assert config.proxy.trust_env is False
    assert config.proxy.http_proxy == ''
    assert config.proxy.https_proxy == ''


def test_grounded_search_response_requires_provider_and_query() -> None:
    response = GroundedSearchResponse(
        provider='perplexity',
        query='OpenClaw web search',
        summary='Grounded summary',
    )
    assert response.provider == 'perplexity'
    assert response.results == []


def test_provider_capabilities_capture_grounding_and_source_controls() -> None:
    caps = SearchProviderCapabilities(
        provider_type=SearchProviderType.GROUNDING,
        supports_search_results=True,
        supports_grounded_summary=True,
        supports_citations=True,
        supports_domain_filtering=True,
        fallback_tier='primary',
    )
    assert caps.provider_type is SearchProviderType.GROUNDING
    assert caps.supports_citations is True
    assert SourceTier.OFFICIAL.value == 'official'
