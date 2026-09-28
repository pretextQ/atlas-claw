# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Browser automation tool powered by Playwright.

This module provides a session-scoped browser manager and a single tool entry
point that supports common actions such as navigation, screenshots, clicks,
typing, evaluation, and DOM inspection during an agent run.

Security notes:
- ``navigate`` only accepts public ``http``/``https`` targets. Non-HTTP
  schemes (``file:``, ``data:``, ...) and private, loopback, or link-local
  hosts are rejected before the browser is invoked. Redirections initiated
  by the remote page itself cannot be intercepted by this guard, so treat
  navigated content as untrusted.
- Each session key owns an isolated browser profile, so cookies and login
  state are never shared across users or conversations.
- Screenshots are returned as base64 data URIs (consistent with the read
  tool) and temporary files are deleted immediately.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
from collections import OrderedDict
from typing import Optional, Any, Callable, TYPE_CHECKING
from urllib.parse import urlsplit

from app.atlasclaw.tools.base import ToolResult
from app.atlasclaw.tools.web.fetch_tool import (
    SSRFBlockedError,
    _assert_http_https_url,
    _assert_public_hostname,
)

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


# Maximum number of session-scoped browser managers kept alive concurrently.
_MAX_ACTIVE_MANAGERS = 4


class BrowserManager:
    """
    Manage a lazily initialized browser instance for a single session.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()

    async def ensure_page(self) -> Any:
        """Return an active Playwright page, creating it on first use."""
        if self._page is not None:
            return self._page

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._page = await self._browser.new_page()
        return self._page

    async def run_action(self, action_factory: Callable[[Any], Any]) -> ToolResult:
        """Serialize one action against this session's page.

        Concurrent tool calls share a single page, so operations must not
        interleave.

        Args:
            action_factory: Callable receiving the page and returning an
                awaitable that produces a ``ToolResult``.

        Returns:
            The produced ``ToolResult``.
        """
        async with self._lock:
            page = await self.ensure_page()
            return await action_factory(page)

    async def cleanup(self) -> None:
        """Close any active page, browser, and Playwright runtime objects."""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def is_active(self) -> bool:
        return self._page is not None


# Session-keyed browser managers. Entries are evicted least-recently-used and
# their Chromium processes are closed on eviction.
_session_managers: "OrderedDict[str, BrowserManager]" = OrderedDict()
_eviction_tasks: set = set()


def get_browser_manager(session_key: str, headless: bool = True) -> BrowserManager:
    """Return the browser manager owning the given session key.

    Args:
        session_key: Stable session identifier; each key gets an isolated
            browser (and therefore isolated cookies and login state).
        headless: Whether the browser runs headless.

    Returns:
        The session-scoped ``BrowserManager``.
    """
    manager = _session_managers.get(session_key)
    if manager is not None:
        _session_managers.move_to_end(session_key)
        return manager

    while len(_session_managers) >= _MAX_ACTIVE_MANAGERS:
        _evicted_key, evicted = _session_managers.popitem(last=False)
        _evict_manager(evicted)

    manager = BrowserManager(headless=headless)
    _session_managers[session_key] = manager
    return manager


def _evict_manager(manager: BrowserManager) -> None:
    """Close an evicted manager's browser in the background."""
    async def _cleanup() -> None:
        try:
            await manager.cleanup()
        except Exception:
            pass

    task = asyncio.create_task(_cleanup())
    _eviction_tasks.add(task)
    task.add_done_callback(_eviction_tasks.discard)


async def cleanup_all_browser_managers() -> None:
    """Close every session-scoped browser; used at application shutdown."""
    managers = list(_session_managers.values())
    _session_managers.clear()
    for manager in managers:
        try:
            await manager.cleanup()
        except Exception:
            pass


async def browser_tool(
    ctx: "RunContext[SkillDeps]",
    action: str,
    url: Optional[str] = None,
    selector: Optional[str] = None,
    text: Optional[str] = None,
    script: Optional[str] = None,
    timeout_ms: int = 30000,
) -> dict:
    """
    Dispatch a browser automation action.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        action: Action name such as `navigate`, `click`, or `screenshot`.
        url: Target URL for `navigate`.
        selector: CSS or XPath selector used by DOM-oriented actions.
        text: Input text for `type` or attribute name for `get_attribute`.
        script: JavaScript source for `evaluate`.
        timeout_ms: Timeout in milliseconds for browser operations.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    start = time.monotonic()
    session_key = getattr(ctx.deps, "session_key", "") or "anonymous"
    manager = get_browser_manager(session_key)

    # Validate navigation targets before any browser is launched so blocked
    # URLs never spawn a Chromium process.
    if action == "navigate" and url:
        try:
            await _assert_navigable_url(url)
        except (SSRFBlockedError, ValueError, RuntimeError) as e:
            return ToolResult.error(
                f"Blocked navigation target: {e}",
                details={"action": action, "status": "blocked", "url": url},
            ).to_dict()

    try:
        result = await manager.run_action(
            lambda page: _dispatch_action(
                page, action, url=url, selector=selector,
                text=text, script=script, timeout_ms=timeout_ms,
            )
        )
    except RuntimeError as e:
        return ToolResult.error(str(e), details={"action": action}).to_dict()
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error(
            str(e),
            details={"action": action, "durationMs": duration_ms, "status": "failed"},
        ).to_dict()

    duration_ms = int((time.monotonic() - start) * 1000)
    result.details["durationMs"] = duration_ms
    result.details["action"] = action
    return result.to_dict()


async def _assert_navigable_url(url: str) -> None:
    """Reject non-HTTP schemes and private/internal navigation targets.

    Raises:
        ValueError: If the URL is not a well-formed http(s) URL.
        SSRFBlockedError: If the host is blocked or resolves internally.
        RuntimeError: If the hostname cannot be resolved.
    """
    _assert_http_https_url(url)
    parsed = urlsplit(url)
    await _assert_public_hostname(parsed.hostname or "")


async def _dispatch_action(
    page: Any,
    action: str,
    *,
    url: Optional[str],
    selector: Optional[str],
    text: Optional[str],
    script: Optional[str],
    timeout_ms: int,
) -> ToolResult:
    """Execute a specific browser action on the active page."""

    if action == "navigate":
        if not url:
            return ToolResult.error("url is required for navigate action")
        # The URL was already validated in browser_tool before the browser
        # was launched; see _assert_navigable_url.
        await page.goto(url, timeout=timeout_ms)
        title = await page.title()
        return ToolResult.text(
            f"Navigated to: {url}",
            details={"status": "completed", "title": title, "url": url},
        )

    if action == "screenshot":
        return await _take_screenshot(page, selector)

    if action == "click":
        if not selector:
            return ToolResult.error("selector is required for click action")
        await page.click(selector, timeout=timeout_ms)
        return ToolResult.text(
            f"Clicked: {selector}",
            details={"status": "completed", "clicked": True, "selector": selector},
        )

    if action == "type":
        if not selector:
            return ToolResult.error("selector is required for type action")
        if text is None:
            return ToolResult.error("text is required for type action")
        await page.fill(selector, text, timeout=timeout_ms)
        return ToolResult.text(
            f"Typed into: {selector}",
            details={"status": "completed", "selector": selector},
        )

    if action == "evaluate":
        if not script:
            return ToolResult.error("script is required for evaluate action")
        eval_result = await page.evaluate(script)
        return ToolResult.text(
            str(eval_result),
            details={"status": "completed"},
        )

    if action == "get_text":
        if not selector:
            return ToolResult.error("selector is required for get_text action")
        element = await page.query_selector(selector)
        if not element:
            return ToolResult.error(f"Element not found: {selector}")
        element_text = await element.text_content()
        return ToolResult.text(
            element_text or "",
            details={"status": "completed", "selector": selector},
        )

    if action == "get_attribute":
        if not selector:
            return ToolResult.error("selector is required for get_attribute action")
        if not text:
            return ToolResult.error("text (attribute name) is required for get_attribute action")
        element = await page.query_selector(selector)
        if not element:
            return ToolResult.error(f"Element not found: {selector}")
        attr_val = await element.get_attribute(text)
        return ToolResult.text(
            attr_val or "",
            details={"status": "completed", "selector": selector, "attribute": text},
        )

    if action == "wait_for":
        if not selector:
            return ToolResult.error("selector is required for wait_for action")
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return ToolResult.text(
                f"Element appeared: {selector}",
                details={"status": "completed", "selector": selector},
            )
        except Exception:
            return ToolResult.error(
                f"Timeout waiting for: {selector}",
                details={"status": "timeout", "selector": selector},
            )

    if action == "scroll":
        direction = text or "down"
        if direction == "down":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction == "up":
            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
        elif direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return ToolResult.text(
            f"Scrolled: {direction}",
            details={"status": "completed", "direction": direction},
        )

    return ToolResult.error(f"Unknown action: {action}")


async def _take_screenshot(page: Any, selector: Optional[str]) -> ToolResult:
    """Capture a screenshot and return it as a base64 data URI.

    The temporary file written for Playwright is deleted before returning so
    no screenshot artifacts accumulate on disk.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="atlasclaw_ss_")
    os.close(fd)
    try:
        if selector:
            element = await page.query_selector(selector)
            if not element:
                return ToolResult.error(f"Element not found: {selector}")
            await element.screenshot(path=tmp_path)
        else:
            await page.screenshot(path=tmp_path, full_page=True)
        with open(tmp_path, "rb") as f:
            raw = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    b64 = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"
    return ToolResult(
        content=[{"type": "image", "url": data_uri}],
        details={"status": "completed", "size_bytes": len(raw)},
    )
