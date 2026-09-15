# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""

web_fetch tool

content.
"""

from __future__ import annotations

import asyncio
from html import unescape
import ipaddress
import logging
import os
import re
import socket
import time
from typing import Optional, TYPE_CHECKING
from urllib.parse import urljoin, urlsplit


from app.atlasclaw.tools.base import ToolResult
from app.atlasclaw.tools.web.text_codec import decode_http_text

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


logger = logging.getLogger(__name__)

DEFAULT_MAX_REDIRECTS = 3
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_FETCH_CACHE_TTL_SECONDS = 15 * 60
DEFAULT_FETCH_CACHE_MAX_ENTRIES = 100
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
CROSS_ORIGIN_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "cookie2",
}
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal")
_FETCH_CACHE: dict[str, tuple[float, str, dict[str, object]]] = {}


class SSRFBlockedError(RuntimeError):
    """Raised when URL fetching is blocked by SSRF safeguards."""


def _browser_like_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _mask_proxy_url(proxy_url: str) -> str:
    if not proxy_url:
        return ""
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        auth = "***@" if parsed.username or parsed.password else ""
        return f"{parsed.scheme}://{auth}{host}{port}"
    except Exception:
        return "***"


def _proxy_debug_info() -> dict[str, str]:
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]
    result: dict[str, str] = {}
    for key in proxy_keys:
        value = os.getenv(key)
        if not value:
            continue
        if key.lower() == "no_proxy":
            result[key] = value
        else:
            result[key] = _mask_proxy_url(value)
    return result


def _env_proxy_enabled() -> bool:
    flag = os.getenv("ATLASCLAW_WEB_USE_PROXY", "").strip().lower()
    if not flag:
        return False
    return flag in {"1", "true", "yes", "on"}


def _normalize_hostname(hostname: str) -> str:
    normalized = (hostname or "").strip().lower().rstrip(".")
    return normalized


def _assert_http_https_url(target_url: str) -> None:
    parsed = urlsplit((target_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Invalid URL: must be http or https")
    if not parsed.hostname:
        raise ValueError("Invalid URL: hostname is required")


def _is_private_or_special_ip(address: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


async def _assert_public_hostname(hostname: str) -> None:
    normalized = _normalize_hostname(hostname)
    if not normalized:
        raise SSRFBlockedError("Blocked hostname: empty")
    if normalized in BLOCKED_HOSTNAMES or normalized.endswith(BLOCKED_HOST_SUFFIXES):
        raise SSRFBlockedError("Blocked hostname or private/internal target")

    try:
        ip_obj = ipaddress.ip_address(normalized)
    except ValueError:
        ip_obj = None
    if ip_obj is not None:
        if _is_private_or_special_ip(str(ip_obj)):
            raise SSRFBlockedError("Blocked hostname or private/internal target")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            normalized,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RuntimeError(f"Unable to resolve hostname: {hostname}") from exc

    addresses = {item[4][0] for item in infos if item and len(item) >= 5 and item[4]}
    if not addresses:
        raise RuntimeError(f"Unable to resolve hostname: {hostname}")
    for address in addresses:
        if _is_private_or_special_ip(address):
            raise SSRFBlockedError("Blocked: resolves to private/internal target")


def _strip_sensitive_headers_for_cross_origin_redirect(headers: dict[str, str]) -> dict[str, str]:
    sanitized = dict(headers)
    for header in CROSS_ORIGIN_SENSITIVE_HEADERS:
        for candidate in (header, header.title()):
            sanitized.pop(candidate, None)
    return sanitized


def _cache_key_for_fetch(
    *,
    url: str,
    extract_mode: str,
    timeout_seconds: float,
    max_redirects: int,
    max_response_bytes: int,
) -> str:
    return "|".join(
        [
            (url or "").strip(),
            extract_mode,
            str(int(timeout_seconds)),
            str(int(max_redirects)),
            str(int(max_response_bytes)),
        ]
    ).lower()


def _read_fetch_cache(cache_key: str) -> tuple[str, dict[str, object]] | None:
    entry = _FETCH_CACHE.get(cache_key)
    if not entry:
        return None
    expires_at, content, details = entry
    if time.time() > expires_at:
        _FETCH_CACHE.pop(cache_key, None)
        return None
    cached_details = dict(details)
    cached_details["cached"] = True
    return content, cached_details


def _write_fetch_cache(
    *,
    cache_key: str,
    content: str,
    details: dict[str, object],
    ttl_seconds: int,
) -> None:
    ttl = max(0, int(ttl_seconds))
    if ttl <= 0:
        return
    if len(_FETCH_CACHE) >= DEFAULT_FETCH_CACHE_MAX_ENTRIES:
        oldest_key = min(_FETCH_CACHE.items(), key=lambda item: item[1][0])[0]
        _FETCH_CACHE.pop(oldest_key, None)
    _FETCH_CACHE[cache_key] = (time.time() + ttl, content, dict(details))


async def _read_response_text_limited(response: object, *, max_bytes: int) -> tuple[str, bool, int]:
    cap = max(1, int(max_bytes))
    chunks: list[bytes] = []
    bytes_read = 0
    truncated = False

    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        projected = bytes_read + len(chunk)
        if projected > cap:
            remaining = max(0, cap - bytes_read)
            if remaining:
                chunks.append(chunk[:remaining])
                bytes_read += remaining
            truncated = True
            break
        chunks.append(chunk)
        bytes_read = projected
        if bytes_read >= cap:
            truncated = True
            break

    body_bytes = b"".join(chunks)
    content_type = ""
    try:
        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("content-type", "") or "")
    except Exception:
        content_type = ""
    declared_encoding = str(getattr(response, "encoding", "") or "")
    text, _encoding_used = decode_http_text(
        body_bytes,
        declared_encoding=declared_encoding,
        content_type=content_type,
    )
    return text, truncated, bytes_read


async def _guarded_get_text(
    *,
    client: object,
    url: str,
    headers: dict[str, str] | None,
    max_redirects: int,
    max_response_bytes: int,
) -> tuple[str, int, str, bool]:
    current_url = (url or "").strip()
    current_headers = dict(headers or {})
    visited: set[str] = set()
    redirects = 0

    while True:
        _assert_http_https_url(current_url)
        parsed = urlsplit(current_url)
        await _assert_public_hostname(parsed.hostname or "")

        async with client.stream(
            "GET",
            current_url,
            headers=current_headers or None,
            follow_redirects=False,
        ) as response:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError(f"Redirect missing location header ({status_code})")
                redirects += 1
                if redirects > max(0, int(max_redirects)):
                    raise RuntimeError(f"Too many redirects (limit: {max_redirects})")
                next_url = urljoin(current_url, location)
                _assert_http_https_url(next_url)
                next_parsed = urlsplit(next_url)
                await _assert_public_hostname(next_parsed.hostname or "")
                if next_url in visited:
                    raise RuntimeError("Redirect loop detected")
                if next_parsed.netloc != parsed.netloc:
                    current_headers = _strip_sensitive_headers_for_cross_origin_redirect(current_headers)
                visited.add(next_url)
                current_url = next_url
                continue

            body_text, truncated, _bytes_read = await _read_response_text_limited(
                response,
                max_bytes=max_response_bytes,
            )
            return body_text, status_code, current_url, truncated


async def _fetch_with_client(
    *,
    url: str,
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_redirects: int,
    max_response_bytes: int,
    trust_env: bool,
) -> tuple[str, int, str, bool]:
    import httpx

    async with httpx.AsyncClient(
        timeout=float(timeout_seconds),
        trust_env=trust_env,
    ) as client:
        return await _guarded_get_text(
            client=client,
            url=url,
            headers=headers,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
        )


async def _guarded_get_text_with_proxy_fallback(
    *,
    url: str,
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_redirects: int,
    max_response_bytes: int,
) -> tuple[str, int, str, bool, bool]:
    allow_env_proxy = _env_proxy_enabled()
    primary_trust_env = bool(allow_env_proxy)
    try:
        body, status_code, final_url, truncated = await _fetch_with_client(
            url=url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
            trust_env=primary_trust_env,
        )
        return body, status_code, final_url, truncated, False
    except SSRFBlockedError:
        raise
    except Exception:
        if not allow_env_proxy:
            raise
        body, status_code, final_url, truncated = await _fetch_with_client(
            url=url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
            trust_env=False,
        )
        return body, status_code, final_url, truncated, True

def _is_informative_extracted_content(content: str) -> bool:
    normalized = " ".join((content or "").split())
    if len(normalized) < 40:
        return False
    lexical = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
    if len(lexical) < 24:
        return False
    return True


def _normalize_reader_payload(payload: str) -> str:
    """Normalize reader-style payloads and strip metadata headers."""
    text = (payload or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    lines = text.split("\n")
    body_lines: list[str] = []
    markdown_block_started = False
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if not markdown_block_started and lowered.startswith("markdown content:"):
            markdown_block_started = True
            remainder = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if remainder:
                body_lines.append(remainder)
            continue
        if markdown_block_started:
            body_lines.append(line)
            continue
        if re.match(r"^(title|url source|published time)\s*:", stripped, flags=re.IGNORECASE):
            continue
        body_lines.append(line)
    normalized = "\n".join(body_lines).strip()
    return normalized or text


def _strip_low_signal_lines(text: str) -> str:
    """Drop obvious navigation/boilerplate lines while keeping factual lines."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""

    kept: list[str] = []
    for line in raw.split("\n"):
        candidate = " ".join(line.split()).strip()
        if not candidate:
            continue
        if re.match(r"^(title|url source|published time|markdown content)\s*:", candidate, flags=re.IGNORECASE):
            continue

        tokens = re.findall(r"\S+", candidate)
        lexical = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", candidate))
        digit_count = len(re.findall(r"\d", candidate))
        url_count = len(re.findall(r"https?://", candidate, flags=re.IGNORECASE))
        nav_symbol_density = len(re.findall(r"[|<>/\\\u00B7\u2022\u203A\u00BB\u2192]", candidate)) / max(1, len(candidate))
        short_ratio = (
            sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens))
            if tokens
            else 0.0
        )

        if url_count >= 2:
            continue
        if lexical < 6 and digit_count == 0:
            continue
        if len(tokens) >= 14 and short_ratio >= 0.72 and digit_count == 0 and nav_symbol_density > 0.015:
            continue
        if len(candidate) >= 120 and digit_count == 0 and short_ratio >= 0.78:
            continue

        kept.append(candidate)

    if not kept:
        return " ".join(raw.split()).strip()

    cleaned = "\n".join(kept).strip()
    cleaned_lexical = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", cleaned))
    raw_lexical = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", raw))
    if cleaned_lexical < 30 and raw_lexical > cleaned_lexical * 3:
        return " ".join(raw.split()).strip()
    return cleaned


def _markdown_to_text(markdown: str) -> str:
    text = _normalize_reader_payload(markdown)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"`{1,3}[^`]*`{1,3}", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>\-\*\+\d\.\)\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return _strip_low_signal_lines(text)


def _looks_like_html(payload: str) -> bool:
    text = (payload or "").strip()
    if not text:
        return False
    return bool(re.search(r"<[a-zA-Z][^>]*>", text))


def _extract_client_side_redirect_url(payload: str, base_url: str) -> str:
    text = (payload or "").strip()
    if not text:
        return ""

    patterns = (
        r'window\.location\.replace\(\s*["\']([^"\']+)["\']\s*\)',
        r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
        r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*url\s*=\s*([^"\'>]+)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        target = unescape((match.group(1) or "").strip().strip("\"'"))
        if not target:
            continue
        return urljoin(base_url, target)
    return ""


def _extract_with_trafilatura(html: str, extract_mode: str) -> tuple[str, str]:
    try:
        import trafilatura
    except Exception:
        return "", ""

    try:
        output_format = "markdown" if extract_mode == "markdown" else "txt"
        extracted = trafilatura.extract(
            html or "",
            output_format=output_format,
            include_comments=False,
            include_tables=True,
            with_metadata=False,
        )
    except Exception:
        return "", ""

    normalized = (extracted or "").strip()
    if not normalized:
        return "", ""
    if extract_mode == "text":
        normalized = _markdown_to_text(normalized)
    if not _is_informative_extracted_content(normalized):
        return "", ""
    return normalized, "trafilatura"


async def _extract_with_crawl4ai(
    url: str,
    *,
    extract_mode: str,
    timeout_seconds: float,
) -> tuple[str, str]:
    try:
        from crawl4ai import AsyncWebCrawler
    except Exception:
        return "", ""

    async def _crawl() -> tuple[str, str]:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            if result is None:
                return "", ""
            markdown = (
                getattr(result, "cleaned_markdown", "")
                or getattr(result, "markdown", "")
                or ""
            )
            if not markdown:
                return "", ""
            content = markdown if extract_mode == "markdown" else _markdown_to_text(markdown)
            if not _is_informative_extracted_content(content):
                return "", ""
            return content, "crawl4ai"

    try:
        return await asyncio.wait_for(_crawl(), timeout=max(1.0, float(timeout_seconds)))
    except Exception:
        return "", ""


async def _fetch_jina_reader(
    url: str,
    timeout_seconds: float,
    *,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> str:
    reader_url = _build_reader_fallback_url(url)
    payload, status_code, _final_url, _truncated, _used_no_proxy = (
        await _guarded_get_text_with_proxy_fallback(
            url=reader_url,
            headers=_browser_like_headers(),
            timeout_seconds=timeout_seconds,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
        )
    )
    if status_code >= 400:
        raise RuntimeError(f"Reader fallback failed ({status_code})")
    return payload


async def fetch_web_content(
    url: str,
    *,
    extract_mode: str = "text",
    timeout_seconds: float = 30.0,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    cache_ttl_seconds: int = DEFAULT_FETCH_CACHE_TTL_SECONDS,
    use_cache: bool = True,
) -> tuple[str, dict[str, object]]:
    """Fetch and extract webpage content for runtime-controlled tool paths."""
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is not installed")

    _assert_http_https_url(url)

    parsed = urlsplit(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    safe_extract_mode = extract_mode if extract_mode in {"text", "markdown", "html"} else "text"
    safe_timeout_seconds = max(1.0, float(timeout_seconds))
    safe_max_response_bytes = max(1024, int(max_response_bytes))
    safe_max_redirects = max(0, int(max_redirects))

    cache_key = _cache_key_for_fetch(
        url=url,
        extract_mode=safe_extract_mode,
        timeout_seconds=safe_timeout_seconds,
        max_redirects=safe_max_redirects,
        max_response_bytes=safe_max_response_bytes,
    )
    if use_cache:
        cached = _read_fetch_cache(cache_key)
        if cached is not None:
            return cached

    fetch_provider = "direct_fetch"
    provider = fetch_provider
    extractor = ""
    status_code = 200
    final_url = url
    resolved_url = url
    response_truncated = False
    html = ""
    request_error: Exception | None = None
    redirect_target = ""

    try:
        body, status_code, final_url, response_truncated, used_no_proxy_fallback = (
            await _guarded_get_text_with_proxy_fallback(
                url=url,
                headers=_browser_like_headers(),
                timeout_seconds=safe_timeout_seconds,
                max_redirects=safe_max_redirects,
                max_response_bytes=safe_max_response_bytes,
            )
        )
        if used_no_proxy_fallback:
            fetch_provider = "direct_fetch_no_proxy_fallback"
            provider = fetch_provider

        if status_code in {401, 403, 412, 429}:
            html = await _fetch_jina_reader(
                url,
                timeout_seconds=min(safe_timeout_seconds, 12.0),
                max_redirects=safe_max_redirects,
                max_response_bytes=safe_max_response_bytes,
            )
            fetch_provider = "jina_reader_fallback"
            provider = fetch_provider
            final_url = _build_reader_fallback_url(url)
            resolved_url = url
            status_code = 200
            response_truncated = False
        elif status_code >= 400:
            raise RuntimeError(f"Web fetch failed ({status_code})")
        else:
            html = body
            redirect_target = _extract_client_side_redirect_url(html, final_url)
            if redirect_target and redirect_target != final_url:
                try:
                    redirected_body, redirected_status, redirected_final_url, redirected_truncated, redirected_used_no_proxy = (
                        await _guarded_get_text_with_proxy_fallback(
                            url=redirect_target,
                            headers=_browser_like_headers(),
                            timeout_seconds=safe_timeout_seconds,
                            max_redirects=safe_max_redirects,
                            max_response_bytes=safe_max_response_bytes,
                        )
                    )
                    if redirected_status < 400:
                        html = redirected_body
                        final_url = redirected_final_url
                        resolved_url = redirected_final_url
                        status_code = redirected_status
                        response_truncated = response_truncated or redirected_truncated
                        if redirected_used_no_proxy:
                            fetch_provider = "direct_fetch_no_proxy_fallback"
                            provider = fetch_provider
                except Exception as exc:
                    request_error = exc
                    resolved_url = redirect_target
    except SSRFBlockedError as exc:
        logger.warning(
            "Web fetch blocked by SSRF guard | url=%s reason=%s",
            url,
            str(exc),
        )
        raise RuntimeError(f"Blocked by SSRF guard: {exc}")
    except Exception as exc:
        request_error = exc

    extraction_url = resolved_url or final_url or redirect_target or url

    if safe_extract_mode in {"text", "markdown"}:
        trafilatura_content = ""
        trafilatura_extractor = ""
        if html:
            trafilatura_content, trafilatura_extractor = _extract_with_trafilatura(html, safe_extract_mode)
        if trafilatura_content:
            content = trafilatura_content
            extractor = trafilatura_extractor
            provider = f"{fetch_provider}+{trafilatura_extractor}"
        else:
            crawl4ai_content, crawl4ai_extractor = await _extract_with_crawl4ai(
                extraction_url,
                extract_mode=safe_extract_mode,
                timeout_seconds=min(max(2.0, safe_timeout_seconds), 12.0),
            )
            if crawl4ai_content:
                content = crawl4ai_content
                extractor = crawl4ai_extractor
                provider = "crawl4ai_fallback"
                resolved_url = extraction_url
            else:
                reader_html = ""
                if fetch_provider != "jina_reader_fallback":
                    try:
                        reader_html = await _fetch_jina_reader(
                            extraction_url,
                            timeout_seconds=min(safe_timeout_seconds, 12.0),
                            max_redirects=safe_max_redirects,
                            max_response_bytes=safe_max_response_bytes,
                        )
                    except Exception:
                        reader_html = ""
                if reader_html:
                    provider = "jina_reader_fallback"
                    reader_payload = _normalize_reader_payload(reader_html)
                    if safe_extract_mode == "markdown":
                        content = (
                            _html_to_markdown(reader_payload)
                            if _looks_like_html(reader_payload)
                            else reader_payload
                        )
                    else:
                        content = (
                            _html_to_text(reader_payload)
                            if _looks_like_html(reader_payload)
                            else _markdown_to_text(reader_payload)
                        )
                    extractor = "jina_reader"
                    resolved_url = extraction_url
                else:
                    if request_error is not None and not html:
                        proxy_info = _proxy_debug_info()
                        logger.warning(
                            "Web fetch request failed | base_url=%s url=%s extract_mode=%s proxy=%s error_type=%s error=%r",
                            base_url,
                            url,
                            safe_extract_mode,
                            proxy_info,
                            type(request_error).__name__,
                            request_error,
                            exc_info=True,
                        )
                        raise RuntimeError(
                            f"Invalid URL or connection error: {type(request_error).__name__}: {request_error!r}"
                        )
                    if safe_extract_mode == "markdown":
                        content = _html_to_markdown(html)
                        extractor = "html_to_markdown"
                    else:
                        content = _html_to_text(html)
                        extractor = "readability_heuristic"
    else:
        content = html
        extractor = "raw_html"
        if request_error is not None and not content:
            proxy_info = _proxy_debug_info()
            logger.warning(
                "Web fetch request failed | base_url=%s url=%s extract_mode=%s proxy=%s error_type=%s error=%r",
                base_url,
                url,
                safe_extract_mode,
                proxy_info,
                type(request_error).__name__,
                request_error,
                exc_info=True,
            )
            raise RuntimeError(f"Invalid URL or connection error: {type(request_error).__name__}: {request_error!r}")

    details: dict[str, object] = {
        "url": url,
        "final_url": final_url,
        "resolved_url": resolved_url,
        "base_url": base_url,
        "extract_mode": safe_extract_mode,
        "truncated": response_truncated,
        "response_truncated": response_truncated,
        "status_code": status_code,
        "provider": provider,
        "fetch_provider": fetch_provider,
        "extractor": extractor,
        "max_response_bytes": safe_max_response_bytes,
        "max_redirects": safe_max_redirects,
        "cached": False,
    }

    if use_cache:
        _write_fetch_cache(
            cache_key=cache_key,
            content=content,
            details=details,
            ttl_seconds=cache_ttl_seconds,
        )

    return content, details

def _build_reader_fallback_url(url: str) -> str:
    normalized = (url or "").strip()
    if normalized.startswith("http://"):
        return "https://r.jina.ai/http://" + normalized[len("http://") :]
    if normalized.startswith("https://"):
        return "https://r.jina.ai/http://" + normalized[len("https://") :]
    return "https://r.jina.ai/http://" + normalized


async def web_fetch_tool(

    ctx: "RunContext[SkillDeps]",
    url: str,
    extract_mode: str = "text",
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    cache_ttl_seconds: int = DEFAULT_FETCH_CACHE_TTL_SECONDS,
) -> dict:
    """

content

    Args:
        ctx:PydanticAI RunContext dependency injection
        url:URL
        extract_mode:mode(text / markdown / html)
        max_response_bytes:max bytes read from response body
        max_redirects:max redirect hops for one fetch request
        cache_ttl_seconds:in-memory cache TTL for repeated URL fetches

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    _ = ctx
    try:
        content, details = await fetch_web_content(
            url,
            extract_mode=extract_mode,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    except Exception as exc:
        return ToolResult.error(
            str(exc),
            details={
                "url": url,
                "extract_mode": extract_mode,
                "max_response_bytes": max_response_bytes,
                "max_redirects": max_redirects,
                "cache_ttl_seconds": cache_ttl_seconds,
            },
        ).to_dict()

    return ToolResult.text(content, details=details).to_dict()



def _html_to_text(html: str) -> str:
    """Extract readable text from HTML, favoring main-content blocks over nav chrome."""
    if not _looks_like_html(html):
        return _markdown_to_text(html)
    rich = _extract_readable_text(html)
    if rich:
        return rich
    return _strip_html_to_text(html)


def _html_to_markdown(html: str) -> str:
    """Convert HTML to lightweight Markdown, using readable extraction when possible."""
    if not _looks_like_html(html):
        return html
    text = _extract_readable_text(html)
    if text:
        return text
    text = html
    for i in range(1, 7):
        text = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, level=i: f"\n{'#' * level} {m.group(1).strip()}\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _extract_readable_text(html: str) -> str:
    """Heuristic main-content extraction inspired by readability-style scoring."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas", "form"]):
        tag.decompose()
    for tag in soup.find_all(["header", "footer", "nav", "aside"]):
        tag.decompose()

    candidates: list[tuple[float, str]] = []
    for node in soup.find_all(["main", "article", "section", "div", "ul", "ol", "table"]):
        text = " ".join(node.stripped_strings)
        if len(text) < 80:
            continue
        link_text = " ".join(a.get_text(" ", strip=True) for a in node.find_all("a"))
        link_density = len(link_text) / max(1, len(text))
        tokens = re.findall(r"\S+", text)
        if not tokens:
            continue
        short_ratio = sum(1 for token in tokens if len(token) <= 2) / max(1, len(tokens))
        punct_count = len(re.findall(r"[\uFF0C\u3002\uFF01\uFF1F\uFF1B;,.!?]", text))
        digit_count = len(re.findall(r"\d", text))
        heading_bonus = 3.0 if node.find(["h1", "h2", "h3"]) else 0.0

        score = (
            (min(len(text), 2200) * 0.012)
            + (punct_count * 0.45)
            + (min(digit_count, 140) * 0.35)
            + heading_bonus
            - (link_density * 120.0)
            - (short_ratio * 18.0)
        )
        if score < 8.0:
            continue
        candidates.append((score, text))

    if not candidates:
        semantic_blocks: list[str] = []
        for node in soup.find_all(["main", "article", "section"]):
            text = " ".join(node.stripped_strings)
            if len(text) < 40:
                continue
            link_text = " ".join(a.get_text(" ", strip=True) for a in node.find_all("a"))
            link_density = len(link_text) / max(1, len(text))
            if link_density > 0.35:
                continue
            semantic_blocks.append(text)
            if len(semantic_blocks) >= 2:
                break
        if not semantic_blocks:
            return ""
        merged = "\n\n".join(semantic_blocks)
        return re.sub(r"\s+", " ", merged).strip()

    candidates.sort(key=lambda item: item[0], reverse=True)
    picked: list[str] = []
    seen_prefix: set[str] = set()
    for _score, text in candidates:
        prefix = text[:160]
        if prefix in seen_prefix:
            continue
        seen_prefix.add(prefix)
        picked.append(text)
        if len(picked) >= 3:
            break

    if not picked:
        return ""
    merged = "\n\n".join(picked)
    merged = re.sub(r"\s+", " ", merged).strip()
    return merged


def _strip_html_to_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
