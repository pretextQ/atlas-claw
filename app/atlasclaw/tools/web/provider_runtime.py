# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlsplit

from app.atlasclaw.tools.web.provider_models import (
    NormalizedSearchResult,
    SearchProviderConfig,
    SearchProviderCapabilities,
    SourceTier,
)


@dataclass
class QueryAttempt:
    """One planned search attempt."""

    query: str
    reason: str


@dataclass
class SearchExecutionResponse:
    """Runtime output for a provider-driven search execution."""

    provider: str
    query: str
    results: list[NormalizedSearchResult]
    summary: str = ""
    citations: list[dict[str, str]] | None = None
    expanded_queries: list[str] | None = None
    retrieved_at: datetime | None = None

    def render_markdown(self) -> str:
        if self.summary:
            lines = [self.summary.strip()]
            if self.citations:
                lines.append("")
                lines.append("Sources:")
                for citation in self.citations:
                    lines.append(f"- [{citation['title']}]({citation['url']})")
            return "\n".join(lines)

        if not self.results:
            return f"Search '{self.query}' returned no results"

        lines: list[str] = []
        for index, result in enumerate(self.results, start=1):
            lines.append(f"{index}. [{result.title}]({result.url})")
            if result.snippet:
                lines.append(f"   {result.snippet}")
        return "\n".join(lines)

    def model_dump(self, mode: str = "python") -> dict[str, object]:
        return {
            "provider": self.provider,
            "query": self.query,
            "summary": self.summary,
            "results": [result.model_dump(mode=mode) for result in self.results],
            "citations": self.citations or [],
            "expanded_queries": self.expanded_queries or [],
            "retrieved_at": (
                self.retrieved_at.isoformat()
                if self.retrieved_at and mode == "json"
                else self.retrieved_at
            ),
        }


class SearchProviderRegistry:
    """Registry of available search providers and their capabilities."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProviderCapabilities] = {}
        self._adapters: dict[str, object] = {}

    def register(self, provider_key: str, capabilities: SearchProviderCapabilities) -> None:
        self._providers[provider_key] = capabilities

    def register_adapter(self, provider_key: str, adapter: object) -> None:
        self._adapters[provider_key] = adapter
        self.register(provider_key, adapter.capabilities)

    def get(self, provider_key: str) -> SearchProviderCapabilities:
        return self._providers[provider_key]

    def get_adapter(self, provider_key: str) -> object:
        return self._adapters[provider_key]

    def keys(self) -> list[str]:
        return list(self._providers.keys())


class SearchProviderSelector:
    """Select the best provider for the current search requirement."""

    def __init__(self, registry: SearchProviderRegistry) -> None:
        self._registry = registry

    def select(
        self,
        *,
        required_grounding: bool,
        requested_provider: str | None,
        degraded_providers: set[str],
    ) -> str:
        if requested_provider and requested_provider not in degraded_providers:
            return requested_provider

        for provider_key in self._registry.keys():
            capabilities = self._registry.get(provider_key)
            if provider_key in degraded_providers:
                continue
            if required_grounding and capabilities.supports_grounded_summary and capabilities.supports_citations:
                return provider_key

        for provider_key in self._registry.keys():
            if provider_key not in degraded_providers:
                return provider_key

        raise RuntimeError("No search provider available")

    def candidate_order(
        self,
        *,
        required_grounding: bool,
        requested_provider: str | None,
        degraded_providers: set[str],
    ) -> list[str]:
        ordered: list[str] = []
        if requested_provider and requested_provider not in degraded_providers:
            ordered.append(requested_provider)
        if required_grounding:
            for provider_key in self._registry.keys():
                if provider_key in degraded_providers or provider_key in ordered:
                    continue
                capabilities = self._registry.get(provider_key)
                if capabilities.supports_grounded_summary and capabilities.supports_citations:
                    ordered.append(provider_key)
        for provider_key in self._registry.keys():
            if provider_key not in degraded_providers and provider_key not in ordered:
                ordered.append(provider_key)
        if not ordered:
            raise RuntimeError("No search provider available")
        return ordered


class SearchQueryPlanner:
    """Plan query attempts for search runtime."""

    def __init__(self, official_domains: list[str]) -> None:
        self._official_domains = official_domains

    def expand(self, query: str, previous_attempts: list[str]) -> list[QueryAttempt]:
        _ = (previous_attempts, self._official_domains)
        # No automatic query rewrite/expansion. Keep user intent exact.
        return [QueryAttempt(query=query, reason="initial")]


class SourcePrioritizer:
    """Assign source tiers and order normalized search results."""

    def __init__(self, official_domains: list[str], trusted_domains: list[str]) -> None:
        self._official_domains = set(official_domains)
        self._trusted_domains = set(trusted_domains)

    def prioritize(self, results: list[NormalizedSearchResult]) -> list[NormalizedSearchResult]:
        normalized: list[NormalizedSearchResult] = []
        for result in results:
            normalized.append(
                result.model_copy(update={"source_tier": self._classify(result.url)})
            )
        # Keep provider rank as the primary ordering; source tiers are metadata only.
        return sorted(normalized, key=lambda item: item.rank)

    def _classify(self, url: str) -> SourceTier:
        host = (urlsplit(url).hostname or "").lower()
        if any(host == domain or host.endswith(f".{domain}") for domain in self._official_domains):
            return SourceTier.OFFICIAL
        if any(host == domain or host.endswith(f".{domain}") for domain in self._trusted_domains):
            return SourceTier.TRUSTED
        return SourceTier.UNKNOWN

    @staticmethod
    def _tier_priority(source_tier: SourceTier) -> int:
        if source_tier is SourceTier.OFFICIAL:
            return 0
        if source_tier is SourceTier.TRUSTED:
            return 1
        if source_tier is SourceTier.COMMUNITY:
            return 2
        return 3


class SearchExecutionRuntime:
    """Execute provider-driven search requests through a registry/selector/planner stack."""

    def __init__(
        self,
        *,
        registry: SearchProviderRegistry,
        selector: SearchProviderSelector,
        planner: SearchQueryPlanner,
        prioritizer: SourcePrioritizer,
        default_provider: str,
        max_query_attempts: int = 3,
        provider_timeout_seconds: float = 6.0,
        provider_hedge_delay_seconds: float = 1.5,
    ) -> None:
        self.registry = registry
        self.selector = selector
        self.planner = planner
        self.prioritizer = prioritizer
        self.default_provider = default_provider
        self.max_query_attempts = max(1, int(max_query_attempts))
        self.provider_timeout_seconds = max(0.5, float(provider_timeout_seconds))
        self.provider_hedge_delay_seconds = max(0.1, float(provider_hedge_delay_seconds))

    async def execute(
        self,
        *,
        query: str,
        provider_override: str | None,
        limit: int,
        require_grounding: bool = False,
        overall_timeout_seconds: float | None = None,
    ) -> SearchExecutionResponse:
        provider_candidates = self.selector.candidate_order(
            required_grounding=require_grounding,
            requested_provider=provider_override or self.default_provider,
            degraded_providers=set(),
        )
        expanded_queries: list[str] = []
        active_query = query
        prioritized_results: list[NormalizedSearchResult] = []
        winning_provider = provider_candidates[0]
        winning_summary = ""
        winning_citations: list[dict[str, str]] = []
        attempts = self.planner.expand(query, previous_attempts=[])
        attempts = attempts[: self.max_query_attempts]
        initial_attempt = attempts[:1]
        expansion_attempts = attempts[1:]
        runtime_timeout = (
            float(overall_timeout_seconds)
            if isinstance(overall_timeout_seconds, (int, float)) and overall_timeout_seconds > 0
            else max(6.0, self.provider_timeout_seconds * 2.0)
        )

        async def _run_attempts(attempt_batch: list[QueryAttempt]) -> bool:
            nonlocal active_query
            nonlocal prioritized_results
            nonlocal winning_provider
            nonlocal winning_summary
            nonlocal winning_citations

            async def _search_with_adapter(
                provider_key: str,
                attempt_query: str,
            ) -> tuple[list[NormalizedSearchResult], str, list[dict[str, str]], bool]:
                adapter = self.registry.get_adapter(provider_key)
                adapter_timeout = float(getattr(adapter, "timeout_seconds", self.provider_timeout_seconds))
                timeout_seconds = min(self.provider_timeout_seconds, max(0.5, adapter_timeout))
                supports_grounded = bool(
                    require_grounding
                    and getattr(getattr(adapter, "capabilities", None), "supports_grounded_summary", False)
                    and getattr(getattr(adapter, "capabilities", None), "supports_citations", False)
                    and hasattr(adapter, "search_grounded")
                )
                if supports_grounded:
                    grounded_response = await asyncio.wait_for(
                        adapter.search_grounded(attempt_query, limit=limit),
                        timeout=timeout_seconds,
                    )
                    if grounded_response is None:
                        return [], "", [], True
                    grounded_results = [
                        _sanitize_search_result(result) for result in grounded_response.results
                    ]
                    grounded_citations = [
                        {"title": item.title, "url": item.url}
                        for item in grounded_response.citations
                    ]
                    return grounded_results, grounded_response.summary, grounded_citations, True

                raw_results = await asyncio.wait_for(adapter.search(attempt_query, limit=limit), timeout=timeout_seconds)
                return [_sanitize_search_result(result) for result in raw_results], "", [], False

            for attempt in attempt_batch:
                if attempt.query not in expanded_queries:
                    expanded_queries.append(attempt.query)
                active_query = attempt.query
                in_flight: dict[asyncio.Task, str] = {
                    asyncio.create_task(_search_with_adapter(provider_key, attempt.query)): provider_key
                    for provider_key in provider_candidates
                }
                provider_bundles: list[
                    tuple[str, list[NormalizedSearchResult], str, list[dict[str, str]], bool]
                ] = []

                try:
                    while in_flight:
                        done, _pending = await asyncio.wait(
                            list(in_flight.keys()),
                            timeout=self.provider_hedge_delay_seconds,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            continue

                        for task in done:
                            provider_key = in_flight.pop(task)
                            try:
                                results, grounded_summary, grounded_citations, from_grounding = task.result()
                            except Exception:
                                continue

                            candidate_results = self.prioritizer.prioritize(results)
                            if not candidate_results:
                                continue

                            if from_grounding and grounded_citations and candidate_results:
                                prioritized_results = candidate_results[:limit]
                                winning_provider = provider_key
                                winning_summary = grounded_summary
                                winning_citations = grounded_citations
                                for pending_task in list(in_flight.keys()):
                                    pending_task.cancel()
                                if in_flight:
                                    await asyncio.gather(*in_flight.keys(), return_exceptions=True)
                                return True

                            provider_bundles.append(
                                (
                                    provider_key,
                                    candidate_results[:limit],
                                    grounded_summary,
                                    grounded_citations,
                                    from_grounding,
                                )
                            )
                finally:
                    if in_flight:
                        for pending_task in list(in_flight.keys()):
                            pending_task.cancel()
                        await asyncio.gather(*in_flight.keys(), return_exceptions=True)

                if provider_bundles:
                    merged_provider, merged_results = _merge_provider_results(
                        provider_candidates=provider_candidates,
                        provider_bundles=provider_bundles,
                        limit=limit,
                    )
                    if merged_results:
                        prioritized_results = merged_results
                        winning_provider = merged_provider or provider_bundles[0][0]
                        winning_summary = ""
                        winning_citations = []
                        return True
            return False

        async def _run_plan() -> None:
            if not await _run_attempts(initial_attempt):
                await _run_attempts(expansion_attempts)

        try:
            await asyncio.wait_for(_run_plan(), timeout=runtime_timeout)
        except asyncio.TimeoutError:
            pass

        return SearchExecutionResponse(
            provider=winning_provider,
            query=active_query,
            results=prioritized_results[:limit],
            summary=winning_summary,
            citations=winning_citations,
            expanded_queries=expanded_queries,
            retrieved_at=datetime.now(),
        )

    @classmethod
    def from_config(cls, config: object) -> "SearchExecutionRuntime":
        registry = SearchProviderRegistry()
        proxy_config = getattr(config, "proxy", None)
        for provider_config in getattr(config, "providers", []):
            if not getattr(provider_config, "enabled", True):
                continue
            adapter = _build_adapter_from_parts(provider_config, proxy_config)
            if adapter is not None:
                registry.register_adapter(provider_config.provider_key, adapter)
        if not registry.keys():
            for provider_config in _fallback_provider_configs():
                adapter = _build_adapter_from_parts(provider_config, proxy_config)
                if adapter is not None:
                    registry.register_adapter(provider_config.provider_key, adapter)
        return cls(
            registry=registry,
            selector=SearchProviderSelector(registry),
            planner=SearchQueryPlanner(getattr(config, "official_domains", [])),
            prioritizer=SourcePrioritizer(
                getattr(config, "official_domains", []),
                getattr(config, "trusted_domains", []),
            ),
            default_provider=getattr(config, "default_provider", "bing_html_fallback"),
            max_query_attempts=getattr(config, "max_query_attempts", 3),
            provider_timeout_seconds=getattr(config, "provider_timeout_seconds", 8.0),
            provider_hedge_delay_seconds=getattr(config, "provider_hedge_delay_seconds", 1.2),
        )


def _fallback_provider_configs() -> list[SearchProviderConfig]:
    return [
        SearchProviderConfig(provider_key="bing_html_fallback", provider_type="search", timeout_seconds=8),
        SearchProviderConfig(provider_key="google_html_fallback", provider_type="search", timeout_seconds=8),
    ]


def _sanitize_search_result(result: NormalizedSearchResult) -> NormalizedSearchResult:
    return result.model_copy(update={"snippet": _sanitize_snippet(result.snippet)})


def _merge_provider_results(
    *,
    provider_candidates: list[str],
    provider_bundles: list[tuple[str, list[NormalizedSearchResult], str, list[dict[str, str]], bool]],
    limit: int,
) -> tuple[str, list[NormalizedSearchResult]]:
    """Pick first non-empty provider by configured order; no relevance scoring."""
    bundles_by_provider: dict[str, list[NormalizedSearchResult]] = {}
    for provider_key, results, _summary, _citations, _from_grounding in provider_bundles:
        if provider_key not in bundles_by_provider:
            bundles_by_provider[provider_key] = []
        for result in results:
            if len(bundles_by_provider[provider_key]) >= limit:
                break
            bundles_by_provider[provider_key].append(
                result.model_copy(update={"provider": provider_key})
            )

    for provider_key in provider_candidates:
        candidates = bundles_by_provider.get(provider_key, [])
        if candidates:
            return provider_key, candidates[:limit]

    if provider_bundles:
        provider_key, results, _summary, _citations, _from_grounding = provider_bundles[0]
        return provider_key, [
            item.model_copy(update={"provider": provider_key}) for item in results[:limit]
        ]
    return "", []


def _score_result_relevance(*, query: str, query_terms: list[str], result: NormalizedSearchResult) -> float:
    title = " ".join((result.title or "").split())
    snippet = " ".join((result.snippet or "").split())
    url = (result.url or "").strip().lower()
    compact_query = re.sub(r"\s+", "", (query or "").lower())

    title_hits = _count_query_term_hits(title, query_terms)
    snippet_hits = _count_query_term_hits(snippet, query_terms)
    url_hits = _count_query_term_hits(url, query_terms)

    score = 0.0
    score += float(title_hits) * 4.0
    score += float(snippet_hits) * 2.0
    score += float(url_hits) * 0.75

    lowered_title = title.lower()
    lowered_snippet = snippet.lower()
    if compact_query and compact_query in (lowered_title + lowered_snippet):
        score += 4.0
    if title_hits >= 2:
        score += 2.0
    if snippet_hits >= 2:
        score += 1.5
    if snippet:
        score += min(len(snippet) / 160.0, 1.0)

    if result.source_tier is SourceTier.OFFICIAL:
        score += 0.9
    elif result.source_tier is SourceTier.TRUSTED:
        score += 0.4

    rank_value = int(result.rank or 99)
    score += max(0.0, 1.5 - min(rank_value, 5) * 0.2)
    return score


def _sanitize_snippet(snippet: str) -> str:
    normalized = " ".join((snippet or "").split())
    if not normalized:
        return ""

    # Common SERP extractions append large navigation payloads after arrows.
    for marker in ("-->", ">>", "->"):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[0].strip()

    # Drop long breadcrumb-style prefixes.
    normalized = re.sub(r"^(?:[^>\s]{1,24}\s*>\s*){2,}", "", normalized).strip()
    normalized = _compress_snippet(normalized, max_chars=240)

    if _looks_like_navigation_noise(normalized):
        return ""

    return normalized


def _extract_query_terms(query: str) -> list[str]:
    normalized = " ".join((query or "").split()).lower()
    if not normalized:
        return []

    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9\-_]{1,}", normalized):
        if len(token) >= 2:
            tokens.append(token)

    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    if cjk_chunks:
        try:
            import jieba

            for word in jieba.cut(" ".join(cjk_chunks)):
                cleaned = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", word or ""))
                if len(cleaned) >= 2:
                    tokens.append(cleaned.lower())
        except Exception:
            for chunk in cjk_chunks:
                if len(chunk) <= 4:
                    tokens.append(chunk)
                else:
                    tokens.append(chunk)
                    tokens.extend(chunk[index : index + 2] for index in range(0, len(chunk) - 1))

    unique_terms: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        unique_terms.append(token)
    return unique_terms[:24]


def _count_query_term_hits(text: str, query_terms: list[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for term in query_terms if term in lowered)


def _compress_snippet(snippet: str, max_chars: int = 240) -> str:
    normalized = " ".join((snippet or "").split()).strip()
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized

    segments = re.split(r"(?<=[。！？!?；;])\s+|\s{2,}", normalized)
    scored: list[tuple[float, str]] = []
    for segment in segments:
        text = segment.strip()
        if len(text) < 12:
            continue
        lexical_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
        if lexical_chars < 8:
            continue
        tokens = re.findall(r"\S+", text)
        short_ratio = (
            sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens))
            if tokens
            else 0.0
        )
        nav_density = len(re.findall(r"[|<>/\\·•›»]", text)) / max(1, len(text))
        punctuation = len(re.findall(r"[，。！？；;,.!?]", text))
        digit_count = len(re.findall(r"\d", text))
        score = (
            (lexical_chars * 0.08)
            + (digit_count * 0.4)
            + (punctuation * 0.25)
            - (short_ratio * 3.6)
            - (nav_density * 25.0)
        )
        scored.append((score, text))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0][1]
        if len(best) > max_chars:
            return best[: max_chars - 3] + "..."
        return best

    return normalized[: max_chars - 3] + "..."


def _looks_like_navigation_noise(text: str) -> bool:
    tokens = [token for token in text.split(" ") if token]
    if len(tokens) < 12:
        return False
    avg_token_len = sum(len(token) for token in tokens) / max(1, len(tokens))
    short_ratio = sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens))
    nav_density = len(re.findall(r"[|<>/\\·•›»]", text)) / max(1, len(text))
    if len(tokens) >= 24 and short_ratio > 0.68 and nav_density > 0.010:
        return True
    digit_count = len(re.findall(r"\d", text))
    has_structured_fact = False
    if digit_count:
        has_structured_fact = any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in (
                r"\d+\s*[-/]\s*\d+",
                r"\d{1,2}[:\uFF1A]\d{2}",
                r"(?:20\d{2}[-/\u5E74])|(?:\d{1,2}[\u6708/-]\d{1,2})",
                r"(?:%|\u2103|\u00B0|km/?h|m/s|mm|cm|hpa|\u00A5|\$|\u5143)",
            )
        )
    if len(tokens) >= 30 and short_ratio > 0.72 and nav_density > 0.008:
        return True
    return avg_token_len <= 4.0 and not has_structured_fact


def _build_adapter_from_config(provider_config: SearchProviderConfig) -> object | None:
    from app.atlasclaw.core.config_schema import SearchProxyConfig

    return _build_adapter_from_parts(provider_config, SearchProxyConfig())


def _build_adapter_from_parts(
    provider_config: SearchProviderConfig,
    proxy_config: object,
) -> object | None:
    from app.atlasclaw.tools.web.provider_adapters import (
        BingHtmlFallbackProvider,
        GoogleHtmlFallbackProvider,
        OpenRouterGroundingProvider,
    )

    provider_trust_env = getattr(provider_config, "trust_env", None)
    adapter_kwargs = {
        "timeout_seconds": provider_config.timeout_seconds,
        "trust_env": (
            provider_trust_env
            if isinstance(provider_trust_env, bool)
            else getattr(proxy_config, "trust_env", False)
        ),
    }

    if provider_config.provider_key == "bing_html_fallback":
        return BingHtmlFallbackProvider(**adapter_kwargs)
    if provider_config.provider_key == "google_html_fallback":
        return GoogleHtmlFallbackProvider(**adapter_kwargs)
    if provider_config.provider_key == "openrouter_grounding":
        return OpenRouterGroundingProvider(
            api_key=_resolve_provider_api_key(provider_config),
            base_url=provider_config.base_url or "https://openrouter.ai/api/v1",
            model=provider_config.model or "perplexity/sonar-pro",
            **adapter_kwargs,
        )
    return None


def _resolve_provider_api_key(provider_config: SearchProviderConfig) -> str:
    if provider_config.api_key:
        return provider_config.api_key
    if provider_config.api_key_env:
        import os

        return os.getenv(provider_config.api_key_env, "")
    return ""


def build_default_search_runtime() -> SearchExecutionRuntime:
    from app.atlasclaw.core.config import get_config

    app_config = get_config()
    runtime = SearchExecutionRuntime.from_config(app_config.search_runtime)
    _register_auto_grounding_adapter(runtime, app_config)
    return runtime


def _register_auto_grounding_adapter(runtime: SearchExecutionRuntime, app_config: object) -> None:
    from app.atlasclaw.tools.web.provider_adapters import OpenRouterGroundingProvider

    model_config = getattr(app_config, "model", None)
    if model_config is None:
        return

    primary_id = str(getattr(model_config, "primary", "")).strip()
    tokens = list(getattr(model_config, "tokens", []) or [])
    primary_token = None
    for token in tokens:
        if str(getattr(token, "id", "")).strip() == primary_id:
            primary_token = token
            break
    if primary_token is None:
        return

    base_url = str(getattr(primary_token, "base_url", "") or "").strip()
    api_key = str(getattr(primary_token, "api_key", "") or "").strip()
    model_name = str(getattr(primary_token, "model", "") or "").strip()
    if "openrouter.ai" not in base_url.lower():
        return
    if not api_key or not model_name:
        return
    if not _model_name_likely_supports_web_grounding(model_name):
        return

    if "openrouter_grounding" in runtime.registry.keys():
        return

    proxy_config = getattr(getattr(app_config, "search_runtime", None), "proxy", None)
    adapter = OpenRouterGroundingProvider(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        timeout_seconds=8,
        trust_env=getattr(proxy_config, "trust_env", False) if proxy_config else False,
    )
    runtime.registry.register_adapter("openrouter_grounding", adapter)
    runtime.default_provider = "openrouter_grounding"


def _model_name_likely_supports_web_grounding(model_name: str) -> bool:
    lowered = (model_name or "").lower()
    hints = ("perplexity", "sonar", "grok", "gemini", "kimi")
    return any(hint in lowered for hint in hints)
