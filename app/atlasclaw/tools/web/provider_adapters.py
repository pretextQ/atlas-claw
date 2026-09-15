# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from app.atlasclaw.tools.web.provider_models import (
    GroundedSearchResponse,
    NormalizedSearchResult,
    SearchCitation,
    SearchProviderCapabilities,
    SearchProviderType,
)
from app.atlasclaw.tools.web.text_codec import decode_http_text

LOGGER = logging.getLogger(__name__)


def _browser_like_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


class BingPageType(str, Enum):
    """Detected page shapes for Bing HTML responses."""

    NORMAL_SERP = "normal_serp"
    ALT_SERP = "alt_serp"
    CHALLENGE = "challenge"
    CONSENT = "consent"
    EMPTY_SHELL = "empty_shell"
    SCRIPT_HEAVY = "script_heavy"
    ERROR_PAGE = "error_page"
    UNKNOWN = "unknown"


@dataclass
class BingPageSignals:
    """Structural signals extracted from one Bing HTML response."""

    title: str = ""
    text_sample: str = ""
    has_b_results: bool = False
    has_b_algo: bool = False
    h2_link_count: int = 0
    anchor_count: int = 0
    external_anchor_count: int = 0
    form_count: int = 0
    script_count: int = 0
    body_text_length: int = 0
    has_challenge_words: bool = False
    has_consent_words: bool = False
    has_error_words: bool = False
    has_search_query_echo: bool = False
    has_result_stats_like: bool = False


@dataclass
class BingPageInspection:
    """Classification result plus supporting signals for a Bing HTML response."""

    page_type: BingPageType
    signals: BingPageSignals = field(default_factory=BingPageSignals)
    reasons: list[str] = field(default_factory=list)
    usable: bool = False


class BaseSearchProviderAdapter:
    """Base adapter for one concrete search provider."""

    provider_key: str
    capabilities: SearchProviderCapabilities

    def __init__(
        self,
        *,
        timeout_seconds: int = 15,
        proxy_url: str = "",
        http_proxy: str = "",
        https_proxy: str = "",
        trust_env: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url
        self.http_proxy = http_proxy
        self.https_proxy = https_proxy
        self.trust_env = trust_env

    def _build_client_kwargs(self) -> dict[str, object]:
        return {
            "follow_redirects": True,
            "timeout": float(self.timeout_seconds),
            "trust_env": self.trust_env,
        }

    def _build_direct_client_kwargs(self) -> dict[str, object]:
        return {
            "follow_redirects": True,
            "timeout": float(self.timeout_seconds),
            "trust_env": False,
        }

    def _proxy_configured(self) -> bool:
        _ = (self.proxy_url, self.http_proxy, self.https_proxy)
        return False

    @staticmethod
    def _has_env_proxy() -> bool:
        proxy_env_names = (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
        return any(bool(os.getenv(name, "").strip()) for name in proxy_env_names)

    def _should_retry_without_proxy(self) -> bool:
        return bool(self.trust_env and self._has_env_proxy())

    async def _fetch_html(self, url: str, headers: Optional[dict[str, str]] = None) -> str:
        async with httpx.AsyncClient(
            **self._build_client_kwargs(),
        ) as client:
            response = await client.get(url, headers=headers or _browser_like_headers())
            response.raise_for_status()
            content_type = str(response.headers.get("content-type", "") or "")
            text, _encoding_used = decode_http_text(
                response.content,
                declared_encoding=str(response.encoding or ""),
                content_type=content_type,
            )
            return text

    async def _fetch_html_no_proxy(self, url: str, headers: Optional[dict[str, str]] = None) -> str:
        async with httpx.AsyncClient(
            **self._build_direct_client_kwargs(),
        ) as client:
            response = await client.get(url, headers=headers or _browser_like_headers())
            response.raise_for_status()
            content_type = str(response.headers.get("content-type", "") or "")
            text, _encoding_used = decode_http_text(
                response.content,
                declared_encoding=str(response.encoding or ""),
                content_type=content_type,
            )
            return text

    async def _fetch_html_with_proxy_retry(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> str:
        try:
            return await self._fetch_html(url, headers=headers)
        except Exception:
            if not self._should_retry_without_proxy():
                raise
            return await self._fetch_html_no_proxy(url, headers=headers)

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        raise NotImplementedError

    async def search_grounded(self, query: str, limit: int = 10) -> GroundedSearchResponse | None:
        _ = (query, limit)
        return None


class OpenRouterGroundingProvider(BaseSearchProviderAdapter):
    """Provider-native grounding search via OpenRouter-compatible chat completions."""

    provider_key = "openrouter_grounding"
    capabilities = SearchProviderCapabilities(
        provider_type=SearchProviderType.GROUNDING,
        supports_search_results=True,
        supports_grounded_summary=True,
        supports_citations=True,
        supports_query_rewrite=False,
        requires_api_key=True,
        fallback_tier="primary",
    )

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "perplexity/sonar-pro",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: int = 15,
        proxy_url: str = "",
        http_proxy: str = "",
        https_proxy: str = "",
        trust_env: bool = False,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            trust_env=trust_env,
        )
        self.api_key = api_key.strip()
        self.model = model.strip() or "perplexity/sonar-pro"
        self.base_url = base_url.rstrip("/")

    async def search_grounded(self, query: str, limit: int = 10) -> GroundedSearchResponse | None:
        if not self.api_key:
            raise RuntimeError("Missing OpenRouter API key for grounding provider")

        system_prompt = (
            "You are a web-grounded search engine. "
            "Return JSON only: {\"summary\": string, \"citations\": [{\"title\": string, \"url\": string, \"snippet\": string}]}. "
            "Use concise summary and include up to the requested citation count. "
            "If evidence is insufficient, keep summary short and citations empty."
        )
        user_prompt = (
            f"query: {query}\n"
            f"max_citations: {max(1, min(limit, 10))}\n"
            "language: match the user query language"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://atlasclaw.local",
            "X-Title": "AtlasClaw Web Grounding",
        }
        response_json = await self._post_json(
            url=f"{self.base_url}/chat/completions",
            payload=payload,
            headers=headers,
        )
        content = _extract_openrouter_content(response_json)
        parsed = _parse_grounding_json(content)

        citations = parsed.get("citations", [])
        if not citations:
            citations = _extract_citations_from_response(response_json, content)
        summary = str(parsed.get("summary", "")).strip() or content.strip()

        normalized_citations: list[SearchCitation] = []
        normalized_results: list[NormalizedSearchResult] = []
        for index, item in enumerate(citations[:limit], start=1):
            title = str(item.get("title", "")).strip() or str(item.get("url", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not url:
                continue
            normalized_citations.append(SearchCitation(title=title or url, url=url))
            normalized_results.append(
                NormalizedSearchResult(
                    title=title or url,
                    url=url,
                    snippet=snippet,
                    provider=self.provider_key,
                    rank=index,
                )
            )

        confidence = 0.85 if normalized_citations else 0.35
        return GroundedSearchResponse(
            provider=self.provider_key,
            query=query,
            summary=summary,
            citations=normalized_citations,
            results=normalized_results,
            confidence=confidence,
        )

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        grounded = await self.search_grounded(query, limit=limit)
        return grounded.results if grounded is not None else []

    async def _post_json(self, *, url: str, payload: dict[str, object], headers: dict[str, str]) -> dict:
        async with httpx.AsyncClient(**self._build_client_kwargs()) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()


class BingHtmlFallbackProvider(BaseSearchProviderAdapter):
    """Fallback adapter for Bing HTML search pages."""

    provider_key = "bing_html_fallback"
    capabilities = SearchProviderCapabilities(
        provider_type=SearchProviderType.SEARCH,
        fallback_tier="fallback",
    )

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        url = (
            f"https://cn.bing.com/search?q={quote_plus(query)}&count={limit}"
            "&setlang=zh-Hans&cc=cn&mkt=zh-CN"
        )
        rss_url = (
            f"https://cn.bing.com/search?format=rss&q={quote_plus(query)}&count={limit}"
            "&setlang=zh-Hans&cc=cn&mkt=zh-CN"
        )
        html = ""
        html_results: list[dict[str, str]] = []
        inspection = BingPageInspection(page_type=BingPageType.UNKNOWN)
        try:
            html = await self._fetch_html(url, _browser_like_headers())
            inspection = _classify_bing_html(html, url)
            html_results = _parse_bing_results(html, limit, query=query, inspection=inspection)
            _log_bing_page_diagnostics(
                query=query,
                stage="proxy",
                url=url,
                inspection=inspection,
                result_count=len(html_results),
                headers=_browser_like_headers(),
            )
        except Exception as exc:
            LOGGER.info(
                "bing_page_fetch_failed stage=proxy query=%r url=%s error=%s",
                query,
                url,
                type(exc).__name__,
            )

        using_direct = False
        attempted_direct = False
        should_retry_direct = self._should_retry_without_proxy() and (
            not html_results
            or inspection.page_type
            in {
                BingPageType.CHALLENGE,
                BingPageType.CONSENT,
                BingPageType.EMPTY_SHELL,
                BingPageType.ERROR_PAGE,
            }
        )
        if should_retry_direct:
            try:
                attempted_direct = True
                html_no_proxy = await self._fetch_html_no_proxy(url, _browser_like_headers())
                direct_inspection = _classify_bing_html(html_no_proxy, url)
                direct_html_results = _parse_bing_results(
                    html_no_proxy,
                    limit,
                    query=query,
                    inspection=direct_inspection,
                )
                _log_bing_page_diagnostics(
                    query=query,
                    stage="direct",
                    url=url,
                    inspection=direct_inspection,
                    result_count=len(direct_html_results),
                    headers=_browser_like_headers(),
                )
                if direct_html_results:
                    html_results = direct_html_results
                    inspection = direct_inspection
                    using_direct = True
            except Exception as exc:
                LOGGER.info(
                    "bing_page_fetch_failed stage=direct query=%r url=%s error=%s",
                    query,
                    url,
                    type(exc).__name__,
                )

        rss_results: list[dict[str, str]] = []
        should_fetch_rss = not html_results or len(html_results) < min(3, limit)
        if should_fetch_rss:
            fetch_rss = self._fetch_html_no_proxy if attempted_direct else self._fetch_html
            try:
                rss_xml = await fetch_rss(rss_url, _browser_like_headers())
                rss_results = _parse_bing_rss_results(rss_xml, limit)
            except Exception:
                rss_results = []

        results = _merge_search_results(query=query, primary=html_results, secondary=rss_results, limit=limit)
        return _normalize_results(self.provider_key, results)


class GoogleHtmlFallbackProvider(BaseSearchProviderAdapter):
    """Fallback adapter for Google HTML search pages."""

    provider_key = "google_html_fallback"
    capabilities = SearchProviderCapabilities(
        provider_type=SearchProviderType.SEARCH,
        fallback_tier="fallback",
    )

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        url = f"https://www.google.com/search?gbv=1&q={quote_plus(query)}&num={limit}"
        html = await self._fetch_html_with_proxy_retry(url, _browser_like_headers())
        parsed = _parse_google_results(html, limit)
        if self._should_retry_without_proxy() and not parsed:
            try:
                html_no_proxy = await self._fetch_html_no_proxy(url, _browser_like_headers())
                direct_parsed = _parse_google_results(html_no_proxy, limit)
                if direct_parsed and not parsed:
                    parsed = direct_parsed
            except Exception:
                pass
        return _normalize_results(self.provider_key, parsed)


def _normalize_results(provider_key: str, results: list[dict[str, str]]) -> list[NormalizedSearchResult]:
    return [
        NormalizedSearchResult(
            title=item["title"],
            url=item["url"],
            snippet=item.get("snippet", ""),
            provider=provider_key,
            rank=index,
        )
        for index, item in enumerate(results, start=1)
    ]


def _merge_search_results(
    *,
    query: str,
    primary: list[dict[str, str]],
    secondary: list[dict[str, str]],
    limit: int,
) -> list[dict[str, str]]:
    scored_candidates: list[tuple[float, int, dict[str, str]]] = []
    query_terms = _extract_query_terms(query)
    for index, item in enumerate(primary):
        candidate = _normalize_candidate_dict(item)
        if not candidate:
            continue
        score = _score_search_candidate(
            query_terms=query_terms,
            candidate=candidate,
            source="primary",
            index=index,
        )
        scored_candidates.append((score, index, candidate))
    for index, item in enumerate(secondary):
        candidate = _normalize_candidate_dict(item)
        if not candidate:
            continue
        score = _score_search_candidate(
            query_terms=query_terms,
            candidate=candidate,
            source="secondary",
            index=index,
        )
        scored_candidates.append((score, index + len(primary), candidate))

    scored_candidates.sort(key=lambda entry: (-entry[0], entry[1]))
    seen_urls: set[str] = set()
    merged: list[dict[str, str]] = []
    for _score, _original_index, candidate in scored_candidates:
        url = candidate["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(candidate)
        if len(merged) >= limit:
            break
    return merged


def _parse_bing_results(
    html: str,
    limit: int,
    *,
    query: str = "",
    inspection: BingPageInspection | None = None,
) -> list[dict[str, str]]:
    inspection = inspection or _classify_bing_html(html, "https://cn.bing.com/search")
    if inspection.page_type is BingPageType.NORMAL_SERP:
        standard_results = _parse_bing_standard_results(html, limit)
        if standard_results:
            return standard_results
        loose_results = _parse_bing_loose_results(html, limit, query=query)
        if loose_results:
            return loose_results
    if inspection.page_type is BingPageType.ALT_SERP:
        loose_results = _parse_bing_loose_results(html, limit, query=query)
        if loose_results:
            return loose_results
    if inspection.page_type is BingPageType.SCRIPT_HEAVY:
        script_results = _parse_bing_script_results(html, limit)
        if script_results:
            return script_results
    if inspection.page_type is BingPageType.UNKNOWN:
        loose_results = _parse_bing_loose_results(html, limit, query=query)
        if loose_results:
            return loose_results
    return []


def _parse_bing_standard_results(html: str, limit: int) -> list[dict[str, str]]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None  # type: ignore[assignment]

    results: list[dict[str, str]] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html or "", "lxml")
        seen_urls: set[str] = set()

        for block in soup.select("li.b_algo"):
            link = block.select_one("h2 a[href]") or block.select_one("a[href]")
            if link is None:
                continue
            href = str(link.get("href", "") or "").strip()
            if not href.startswith(("http://", "https://")):
                continue
            title = " ".join(link.get_text(" ", strip=True).split()).strip()
            if not title or href in seen_urls:
                continue
            snippet_node = (
                block.select_one(".b_caption p")
                or block.select_one(".b_snippet")
                or block.select_one("p")
            )
            snippet = ""
            if snippet_node is not None:
                snippet = " ".join(snippet_node.get_text(" ", strip=True).split()).strip()
            seen_urls.add(href)
            results.append({"title": title, "url": href, "snippet": snippet})
            if len(results) >= limit:
                break

    if results:
        return results[:limit]

    blocks = re.findall(
        r'<li\s+class="b_algo"[^>]*>(.*?)</li>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    for block in blocks[:limit]:
        link_match = re.search(r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_match:
            continue
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        results.append(
            {
                "title": re.sub(r"<[^>]+>", "", link_match.group(2)).strip(),
                "url": link_match.group(1),
                "snippet": re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                if snippet_match
                else "",
            }
        )
    if results:
        return [item for item in results if item["title"] and item["url"]]
    return []


def _parse_bing_loose_results(html: str, limit: int, *, query: str) -> list[dict[str, str]]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []

    soup = BeautifulSoup(html or "", "lxml")
    root = soup.select_one("#b_results") or soup.select_one("main") or soup.body or soup
    candidates: list[tuple[float, dict[str, str]]] = []
    seen_urls: set[str] = set()
    query_terms = _extract_query_terms(query)
    for anchor in root.select("a[href]"):
        href = str(anchor.get("href", "") or "").strip()
        if not _looks_like_external_result_href(href):
            continue
        normalized_href = href
        title = _extract_anchor_title(anchor)
        if not title:
            continue
        if normalized_href in seen_urls:
            continue
        container = _find_meaningful_container(anchor)
        snippet = _extract_container_snippet(container, anchor, title)
        candidate = {"title": title, "url": normalized_href, "snippet": snippet}
        score = _score_loose_candidate(
            query_terms=query_terms,
            candidate=candidate,
            anchor=anchor,
            container=container,
        )
        if score <= 0:
            continue
        seen_urls.add(normalized_href)
        candidates.append((score, candidate))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _score, candidate in candidates[:limit]]


def _parse_bing_script_results(html: str, limit: int) -> list[dict[str, str]]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []

    soup = BeautifulSoup(html or "", "lxml")
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for script in soup.select('script[type="application/ld+json"]'):
        raw_text = script.string or script.get_text(" ", strip=True)
        if not raw_text:
            continue
        for payload in _iter_json_payloads(raw_text):
            for candidate in _extract_jsonld_result_candidates(payload):
                url = candidate["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(candidate)
                if len(results) >= limit:
                    return results
    return results


def _parse_bing_rss_results(rss_xml: str, limit: int) -> list[dict[str, str]]:
    try:
        root = ElementTree.fromstring(rss_xml)
    except ElementTree.ParseError:
        return []
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        snippet = (item.findtext("description") or "").strip()
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


_CHALLENGE_WORDS = (
    "verify",
    "verification",
    "captcha",
    "challenge",
    "security check",
    "unusual traffic",
    "blocked",
    "automated queries",
)

_CONSENT_WORDS = (
    "consent",
    "privacy",
    "cookie",
    "accept",
    "preferences",
    "region",
)

_ERROR_WORDS = (
    "something went wrong",
    "temporarily unavailable",
    "forbidden",
    "access denied",
    "error",
)

_SEARCH_ENGINE_HOSTS = (
    "bing.com",
    "www.bing.com",
    "cn.bing.com",
    "google.com",
    "www.google.com",
    "microsoft.com",
    "www.microsoft.com",
)


def _normalize_candidate_dict(item: dict[str, str]) -> dict[str, str] | None:
    title = _collapse_whitespace(str(item.get("title", "")).strip())
    url = str(item.get("url", "")).strip()
    snippet = _collapse_whitespace(str(item.get("snippet", "")).strip())
    if not title or not url:
        return None
    return {"title": title, "url": url, "snippet": snippet}


def _score_search_candidate(
    *,
    query_terms: list[str],
    candidate: dict[str, str],
    source: str,
    index: int,
) -> float:
    title = candidate["title"]
    snippet = candidate.get("snippet", "")
    combined = f"{title} {snippet}".strip()
    title_hits = _count_query_term_hits(title, query_terms)
    body_hits = _count_query_term_hits(combined, query_terms)
    lexical_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", combined))
    snippet_len = len(snippet)
    base = 38.0 if source == "primary" else 26.0
    score = (
        base
        + min(title_hits, 6) * 8.0
        + min(body_hits, 8) * 3.5
        + min(snippet_len, 200) * 0.03
        + min(lexical_chars, 180) * 0.02
        - index * 1.2
    )
    if not snippet:
        score -= 4.0
    if _looks_like_search_engine_host(candidate["url"]):
        score -= 8.0
    if _looks_like_navigation_text(combined):
        score -= 10.0
    return score


def _score_loose_candidate(
    *,
    query_terms: list[str],
    candidate: dict[str, str],
    anchor: object,
    container: object,
) -> float:
    title = candidate["title"]
    snippet = candidate.get("snippet", "")
    score = _score_search_candidate(
        query_terms=query_terms,
        candidate=candidate,
        source="primary",
        index=0,
    )
    anchor_parent_name = getattr(getattr(anchor, "parent", None), "name", "")
    if anchor_parent_name in {"h1", "h2", "h3"}:
        score += 6.0
    container_text = _collapse_whitespace(
        getattr(container, "get_text", lambda *args, **kwargs: "")(" ", strip=True)
    )
    if len(container_text) >= 40:
        score += 3.0
    if snippet:
        score += 3.0
    return score


def _extract_query_terms(query: str) -> list[str]:
    normalized = _collapse_whitespace(query).lower()
    if not normalized:
        return []
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9\-_]{1,}", normalized):
        if len(token) >= 2:
            tokens.append(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if len(chunk) <= 4:
            tokens.append(chunk)
        else:
            tokens.append(chunk)
            tokens.extend(chunk[index : index + 2] for index in range(0, len(chunk) - 1))
            tokens.extend(chunk[index : index + 3] for index in range(0, len(chunk) - 2))
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


def _looks_like_navigation_text(text: str) -> bool:
    normalized = _collapse_whitespace(text)
    if not normalized:
        return False
    tokens = [token for token in normalized.split(" ") if token]
    if len(tokens) < 8:
        return False
    short_ratio = sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens))
    nav_density = len(re.findall(r"[|<>/\\·•›»]", normalized)) / max(1, len(normalized))
    return short_ratio > 0.62 and nav_density > 0.01


def _looks_like_external_result_href(href: str) -> bool:
    if not href.startswith(("http://", "https://")):
        return False
    host = (urlsplit(href).hostname or "").lower()
    if not host:
        return False
    return not _looks_like_search_engine_host(href)


def _looks_like_search_engine_host(href: str) -> bool:
    host = (urlsplit(href).hostname or "").lower()
    return any(host == engine_host or host.endswith(f".{engine_host}") for engine_host in _SEARCH_ENGINE_HOSTS)


def _extract_anchor_title(anchor: object) -> str:
    title = _collapse_whitespace(getattr(anchor, "get_text", lambda *args, **kwargs: "")(" ", strip=True))
    lexical_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", title))
    return title if lexical_chars >= 4 else ""


def _find_meaningful_container(anchor: object) -> object:
    current = getattr(anchor, "parent", None)
    best = current or anchor
    best_score = -1.0
    for _ in range(6):
        if current is None:
            break
        text = _collapse_whitespace(getattr(current, "get_text", lambda *args, **kwargs: "")(" ", strip=True))
        lexical_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
        if 24 <= lexical_chars <= 1200:
            tag_name = getattr(current, "name", "")
            score = 0.0
            if tag_name in {"article", "section", "li"}:
                score += 4.0
            if tag_name in {"div", "main"}:
                score += 2.0
            score += min(lexical_chars, 400) * 0.01
            if score > best_score:
                best = current
                best_score = score
        current = getattr(current, "parent", None)
    return best


def _extract_container_snippet(container: object, anchor: object, title: str) -> str:
    snippet = ""
    for selector in ("p", ".b_caption", ".caption", ".snippet", ".summary"):
        if hasattr(container, "select_one"):
            node = container.select_one(selector)
            if node is not None:
                snippet = _collapse_whitespace(node.get_text(" ", strip=True))
                break
    if not snippet:
        block_text = _collapse_whitespace(
            getattr(container, "get_text", lambda *args, **kwargs: "")(" ", strip=True)
        )
        anchor_text = _collapse_whitespace(
            getattr(anchor, "get_text", lambda *args, **kwargs: "")(" ", strip=True)
        )
        snippet = block_text
        if anchor_text and anchor_text in snippet:
            snippet = snippet.replace(anchor_text, "", 1).strip()
        elif title and title in snippet:
            snippet = snippet.replace(title, "", 1).strip()
    snippet = _collapse_whitespace(snippet)
    if len(snippet) > 220:
        snippet = snippet[:217].rstrip() + "..."
    return snippet


def _iter_json_payloads(raw_text: str) -> list[object]:
    text = (raw_text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _extract_jsonld_result_candidates(payload: object) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            url = str(node.get("url", "")).strip()
            title = _collapse_whitespace(str(node.get("name", "") or node.get("headline", "")).strip())
            description = _collapse_whitespace(str(node.get("description", "")).strip())
            if url and title and _looks_like_external_result_href(url):
                candidates.append({"title": title, "url": url, "snippet": description})
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return candidates


def _extract_html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _collapse_whitespace(unescape(re.sub(r"<[^>]+>", "", match.group(1))))


def _extract_query_echo_from_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except Exception:
        return ""
    query_values = parse_qs(parsed.query).get("q", [])
    if not query_values:
        return ""
    return _collapse_whitespace(unquote(query_values[0]))


def _collapse_whitespace(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _strip_non_content_blocks(html: str) -> str:
    text = html or ""
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<noscript\b[^>]*>.*?</noscript>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def _contains_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in phrases)


def _log_bing_page_diagnostics(
    *,
    query: str,
    stage: str,
    url: str,
    inspection: BingPageInspection,
    result_count: int,
    headers: dict[str, str],
) -> None:
    LOGGER.info(
        "bing_page_inspection stage=%s query=%r url=%s fetch_success=%s page_type=%s "
        "page_usable=%s extraction_success=%s results=%d title=%r html_len=%d "
        "has_b_results=%s has_b_algo=%s h2_links=%d external_links=%d forms=%d scripts=%d "
        "accept_language=%r cookie_present=%s reasons=%s",
        stage,
        query,
        url,
        True,
        inspection.page_type.value,
        inspection.usable,
        result_count > 0,
        result_count,
        inspection.signals.title,
        inspection.signals.body_text_length,
        inspection.signals.has_b_results,
        inspection.signals.has_b_algo,
        inspection.signals.h2_link_count,
        inspection.signals.external_anchor_count,
        inspection.signals.form_count,
        inspection.signals.script_count,
        headers.get("Accept-Language", ""),
        bool(headers.get("Cookie")),
        ",".join(inspection.reasons),
    )


def _classify_bing_html(
    html: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
) -> BingPageInspection:
    signals = _collect_bing_page_signals(html, url, headers=headers)
    reasons: list[str] = []

    if signals.has_challenge_words:
        reasons.append("challenge-words")
        return BingPageInspection(
            page_type=BingPageType.CHALLENGE,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.has_consent_words and signals.external_anchor_count < 2:
        reasons.append("consent-words")
        return BingPageInspection(
            page_type=BingPageType.CONSENT,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.has_error_words and signals.external_anchor_count == 0:
        reasons.append("error-words")
        return BingPageInspection(
            page_type=BingPageType.ERROR_PAGE,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.has_b_algo:
        reasons.append("b_algo")
        return BingPageInspection(
            page_type=BingPageType.NORMAL_SERP,
            signals=signals,
            reasons=reasons,
            usable=True,
        )
    if signals.has_b_results and signals.external_anchor_count >= 1 and signals.h2_link_count >= 1:
        reasons.append("b_results-with-links")
        return BingPageInspection(
            page_type=BingPageType.NORMAL_SERP,
            signals=signals,
            reasons=reasons,
            usable=True,
        )
    if signals.h2_link_count >= 2 and signals.external_anchor_count >= 2:
        reasons.append("repeated-heading-links")
        return BingPageInspection(
            page_type=BingPageType.ALT_SERP,
            signals=signals,
            reasons=reasons,
            usable=True,
        )
    if signals.script_count >= 18 and signals.external_anchor_count <= 2 and signals.body_text_length < 900:
        reasons.append("script-heavy-shell")
        return BingPageInspection(
            page_type=BingPageType.SCRIPT_HEAVY,
            signals=signals,
            reasons=reasons,
            usable=True,
        )
    if (
        signals.form_count >= 1
        and signals.h2_link_count == 0
        and not signals.has_b_results
        and not signals.has_result_stats_like
        and signals.external_anchor_count <= 12
    ):
        reasons.append("search-form-without-result-blocks")
        return BingPageInspection(
            page_type=BingPageType.EMPTY_SHELL,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.form_count >= 1 and signals.external_anchor_count == 0 and signals.body_text_length < 320:
        reasons.append("search-form-without-results")
        return BingPageInspection(
            page_type=BingPageType.EMPTY_SHELL,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.external_anchor_count == 0 and signals.body_text_length < 220:
        reasons.append("empty-body")
        return BingPageInspection(
            page_type=BingPageType.EMPTY_SHELL,
            signals=signals,
            reasons=reasons,
            usable=False,
        )
    if signals.external_anchor_count >= 2:
        reasons.append("generic-external-links")
        return BingPageInspection(
            page_type=BingPageType.ALT_SERP,
            signals=signals,
            reasons=reasons,
            usable=True,
        )
    reasons.append("no-recognized-structure")
    return BingPageInspection(
        page_type=BingPageType.UNKNOWN,
        signals=signals,
        reasons=reasons,
        usable=False,
    )


def _collect_bing_page_signals(
    html: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
) -> BingPageSignals:
    _ = headers
    cleaned_html = _strip_non_content_blocks(html)
    body_text = _collapse_whitespace(_strip_tags(cleaned_html))
    title = _extract_html_title(html)
    query_echo = _extract_query_echo_from_url(url)

    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None  # type: ignore[assignment]

    if BeautifulSoup is None:
        return BingPageSignals(
            title=title,
            text_sample=body_text[:400],
            has_b_results='id="b_results"' in cleaned_html or "id='b_results'" in cleaned_html,
            has_b_algo='class="b_algo"' in cleaned_html or "class='b_algo'" in cleaned_html,
            h2_link_count=len(re.findall(r"<h2[^>]*>\s*<a\s+[^>]*href=", cleaned_html, re.IGNORECASE)),
            anchor_count=len(re.findall(r"<a\s+[^>]*href=", cleaned_html, re.IGNORECASE)),
            external_anchor_count=len(re.findall(r'href="https?://', cleaned_html, re.IGNORECASE)),
            form_count=len(re.findall(r"<form\b", cleaned_html, re.IGNORECASE)),
            script_count=len(re.findall(r"<script\b", html, re.IGNORECASE)),
            body_text_length=len(body_text),
            has_challenge_words=_contains_any_phrase(body_text, _CHALLENGE_WORDS),
            has_consent_words=_contains_any_phrase(body_text, _CONSENT_WORDS),
            has_error_words=_contains_any_phrase(body_text, _ERROR_WORDS),
            has_search_query_echo=bool(query_echo and query_echo.lower() in f"{title} {body_text[:800]}".lower()),
            has_result_stats_like=bool(re.search(r"\b\d[\d,\.]*\s+(?:results?|条结果)\b", body_text, re.IGNORECASE)),
        )

    soup = BeautifulSoup(cleaned_html or "", "lxml")
    text = _collapse_whitespace(soup.get_text(" ", strip=True))
    anchors = soup.select("a[href]")
    external_anchor_count = sum(
        1 for anchor in anchors if _looks_like_external_result_href(str(anchor.get("href", "") or "").strip())
    )
    return BingPageSignals(
        title=title or _collapse_whitespace(soup.title.get_text(" ", strip=True)) if soup.title else title,
        text_sample=text[:400],
        has_b_results=bool(soup.select_one("#b_results")),
        has_b_algo=bool(soup.select_one(".b_algo")),
        h2_link_count=len(soup.select("h2 a[href]")),
        anchor_count=len(anchors),
        external_anchor_count=external_anchor_count,
        form_count=len(soup.select("form")),
        script_count=len(soup.select("script")),
        body_text_length=len(text),
        has_challenge_words=_contains_any_phrase(text, _CHALLENGE_WORDS),
        has_consent_words=_contains_any_phrase(text, _CONSENT_WORDS),
        has_error_words=_contains_any_phrase(text, _ERROR_WORDS),
        has_search_query_echo=bool(query_echo and query_echo.lower() in f"{title} {text[:800]}".lower()),
        has_result_stats_like=bool(re.search(r"\b\d[\d,\.]*\s+(?:results?|条结果)\b", text, re.IGNORECASE)),
    )


def _parse_google_results(html: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    blocks = re.findall(r'<div\s+class="g"[^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
    for block in blocks[:limit]:
        title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        link_match = re.search(r'<a\s+[^>]*href="([^"]+)"', block, re.DOTALL | re.IGNORECASE)
        if not title_match or not link_match:
            continue
        normalized_href = _unwrap_google_href(link_match.group(1))
        if not normalized_href:
            continue
        snippet_match = re.search(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
        results.append(
            {
                "title": re.sub(r"<[^>]+>", "", title_match.group(1)).strip(),
                "url": normalized_href,
                "snippet": re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip() if snippet_match else "",
            }
        )
    if results:
        return [item for item in results if item["title"] and item["url"]]

    generic_links = re.findall(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    for href, title_html in generic_links[:limit]:
        normalized_href = _unwrap_google_href(href)
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        if title and normalized_href:
            results.append({"title": title, "url": normalized_href, "snippet": ""})
    return [item for item in results if item["title"] and item["url"]]


def _unwrap_google_href(href: str) -> str:
    if not href:
        return ""
    normalized = urljoin("https://www.google.com", href)
    parsed = urlsplit(normalized)
    if parsed.path == "/url":
        query = parse_qs(parsed.query)
        target = query.get("q", [])
        if target:
            return unquote(target[0])
        return ""
    if normalized.startswith("https://") or normalized.startswith("http://"):
        return normalized
    return ""


def _extract_openrouter_content(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "\n".join(chunks).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_grounding_json(content: str) -> dict:
    text = (content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    snippet = text[start : end + 1]
    try:
        parsed = json.loads(snippet)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_citations_from_response(payload: dict, content: str) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    raw_citations = payload.get("citations")
    if isinstance(raw_citations, list):
        for item in raw_citations:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                citations.append({"title": item, "url": item, "snippet": ""})
            elif isinstance(item, dict):
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                title = str(item.get("title", "")).strip() or url
                snippet = str(item.get("snippet", "")).strip()
                citations.append({"title": title, "url": url, "snippet": snippet})
    if citations:
        return citations

    markdown_links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", content or "")
    for title, url in markdown_links:
        citations.append({"title": title.strip() or url, "url": url.strip(), "snippet": ""})
    return citations
