# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SearchProviderType(str, Enum):
    """Supported search provider classes."""

    SEARCH = "search"
    GROUNDING = "grounding"


class SourceTier(str, Enum):
    """Trust tiers for normalized search results."""

    OFFICIAL = "official"
    TRUSTED = "trusted"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class SearchProviderCapabilities(BaseModel):
    """Capability contract exposed by a search or grounding provider."""

    provider_type: SearchProviderType
    supports_search_results: bool = True
    supports_grounded_summary: bool = False
    supports_citations: bool = False
    supports_domain_filtering: bool = False
    supports_language_hint: bool = False
    supports_recency_hint: bool = False
    supports_query_rewrite: bool = False
    requires_api_key: bool = False
    supports_cache: bool = True
    fallback_tier: str = "fallback"


class NormalizedSearchResult(BaseModel):
    """Provider-agnostic search result shape."""

    title: str
    url: str
    snippet: str = ""
    provider: str
    rank: int = 0
    source_tier: SourceTier = SourceTier.UNKNOWN
    language: str = ""
    confidence: float = 0.0


class SearchCitation(BaseModel):
    """Minimal citation structure for grounded providers."""

    title: str
    url: str


class GroundedSearchResponse(BaseModel):
    """Unified grounded search response."""

    provider: str
    query: str
    summary: str = ""
    citations: list[SearchCitation] = Field(default_factory=list)
    results: list[NormalizedSearchResult] = Field(default_factory=list)
    confidence: float = 0.0


class SearchProviderConfig(BaseModel):
    """Configuration record for one registered search provider."""

    provider_key: str
    enabled: bool = True
    provider_type: SearchProviderType
    api_key_env: str = ""
    api_key: str = ""
    timeout_seconds: int = 15
    base_url: str = ""
    model: str = ""
    fallback_tier: str = "fallback"
    trust_env: bool | None = None
