# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the Playwright browser tool."""

from __future__ import annotations

import base64
import glob
import os
import tempfile
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import app.atlasclaw.tools.ui.browser_tool as browser_tool_module
from app.atlasclaw.tools.ui.browser_tool import (
    browser_tool,
    cleanup_all_browser_managers,
    get_browser_manager,
)


class FakePage:
    """Minimal Playwright page double."""

    def __init__(self, screenshot_bytes: bytes = b"png-bytes"):
        self.navigated_urls = []
        self.screenshot_paths = []
        self._screenshot_bytes = screenshot_bytes
        self.closed = False

    async def goto(self, url, timeout=None):
        self.navigated_urls.append(url)

    async def title(self):
        return "Example"

    async def screenshot(self, path=None, full_page=False):
        self.screenshot_paths.append(path)
        with open(path, "wb") as f:
            f.write(self._screenshot_bytes)

    async def close(self):
        self.closed = True


class FakeManager:
    """Manager double that dispatches actions against a FakePage."""

    def __init__(self):
        self.page = FakePage()

    async def run_action(self, action_factory):
        return await action_factory(self.page)


def _ctx(session_key: str = "session-1"):
    return SimpleNamespace(deps=SimpleNamespace(session_key=session_key))


@pytest.fixture(autouse=True)
def clean_manager_registry(monkeypatch):
    """Isolate the module-level manager registry for every test."""
    monkeypatch.setattr(browser_tool_module, "_session_managers", OrderedDict())
    monkeypatch.setattr(browser_tool_module, "_eviction_tasks", set())
    yield


@pytest.fixture
def fake_manager(monkeypatch):
    """Route the tool at a fake manager instead of launching Chromium."""
    manager = FakeManager()
    monkeypatch.setattr(
        browser_tool_module, "get_browser_manager", lambda *a, **k: manager
    )
    return manager


@pytest.mark.asyncio
async def test_navigate_blocks_file_scheme():
    """file:// URLs must never reach the browser."""
    result = await browser_tool(_ctx(), "navigate", url="file:///etc/passwd")

    assert result["is_error"] is True
    assert "Blocked navigation target" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_navigate_blocks_data_scheme():
    """data: URLs must never reach the browser."""
    result = await browser_tool(_ctx(), "navigate", url="data:text/html,hello")

    assert result["is_error"] is True
    assert "Blocked navigation target" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_navigate_blocks_metadata_ip():
    """Cloud metadata and other link-local targets must be blocked."""
    result = await browser_tool(_ctx(), "navigate", url="http://169.254.169.254/latest/meta-data/")

    assert result["is_error"] is True
    assert "Blocked navigation target" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_navigate_blocks_loopback():
    """Loopback targets must be blocked."""
    result = await browser_tool(_ctx(), "navigate", url="http://127.0.0.1:8000/admin")

    assert result["is_error"] is True
    assert "Blocked navigation target" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_navigate_allows_public_target(fake_manager):
    """Public IP literal targets are navigable without DNS lookups."""
    result = await browser_tool(_ctx(), "navigate", url="https://93.184.216.34/")

    assert result["is_error"] is False
    assert fake_manager.page.navigated_urls == ["https://93.184.216.34/"]


@pytest.mark.asyncio
async def test_screenshot_returns_data_uri_and_removes_temp_file(fake_manager):
    """Screenshots come back as data URIs and leave no temp files behind."""
    tmp_dir = tempfile.gettempdir()
    before = set(glob.glob(os.path.join(tmp_dir, "atlasclaw_ss_*")))

    result = await browser_tool(_ctx(), "screenshot")

    assert result["is_error"] is False
    image = result["content"][0]
    assert image["type"] == "image"
    assert image["url"].startswith("data:image/png;base64,")

    encoded = image["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"png-bytes"

    after = set(glob.glob(os.path.join(tmp_dir, "atlasclaw_ss_*")))
    assert after == before


def test_browser_managers_are_isolated_per_session():
    """Different session keys own different browser instances."""
    first = get_browser_manager("session-a")
    second = get_browser_manager("session-b")
    first_again = get_browser_manager("session-a")

    assert isinstance(first, browser_tool_module.BrowserManager)
    assert first is not second
    assert first is first_again


@pytest.mark.asyncio
async def test_evicted_session_manager_is_cleaned_up(monkeypatch):
    """LRU eviction closes the evicted session's browser."""
    monkeypatch.setattr(browser_tool_module, "_MAX_ACTIVE_MANAGERS", 2)

    evicted = get_browser_manager("session-0")
    page = FakePage()
    evicted._page = page

    get_browser_manager("session-1")
    get_browser_manager("session-2")  # evicts session-0 (least recently used)

    assert "session-0" not in browser_tool_module._session_managers
    for task in list(browser_tool_module._eviction_tasks):
        await task

    assert page.closed is True
    assert evicted._page is None


@pytest.mark.asyncio
async def test_cleanup_all_browser_managers_closes_every_session():
    """Shutdown cleanup closes all session browsers and empties the registry."""
    first = get_browser_manager("session-a")
    second = get_browser_manager("session-b")
    page_a, page_b = FakePage(), FakePage()
    first._page = page_a
    second._page = page_b

    await cleanup_all_browser_managers()

    assert page_a.closed is True
    assert page_b.closed is True
    assert browser_tool_module._session_managers == {}
