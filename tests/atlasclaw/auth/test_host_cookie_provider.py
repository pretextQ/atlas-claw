# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for HostCookieAuthProvider validate_url enforcement."""

from __future__ import annotations

import httpx
import pytest

from app.atlasclaw.auth.models import AuthenticationError
from app.atlasclaw.auth.providers.host_cookie import HostCookieAuthProvider

pytestmark = pytest.mark.asyncio


def _provider(validate_url: str = "", client: httpx.AsyncClient | None = None):
    return HostCookieAuthProvider(
        provider_name="host_cookie",
        token_cookie_name="Host-Token",
        subject_cookie_name="userLoginId",
        display_name_cookie_name="username",
        user_id_cookie_name="userId",
        tenant_id_cookie_name="tenant_id",
        validate_url=validate_url,
        http_client=client,
    )


def _cookies(subject: str = "alice") -> dict:
    return {
        "Host-Token": "host-session-token",
        "userLoginId": subject,
        "username": "Alice",
        "userId": "u-1",
        "tenant_id": "t-1",
    }


class TestHostCookieWithoutValidateUrl:
    async def test_cookies_are_trusted_as_before(self):
        result = await _provider().authenticate_from_cookies(_cookies())
        assert result.subject == "alice"
        assert result.extra["auth_type"] == "cookie"

    async def test_missing_token_is_rejected(self):
        with pytest.raises(AuthenticationError):
            await _provider().authenticate_from_cookies({"userLoginId": "alice"})


class TestHostCookieWithValidateUrl:
    def _client(self, handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_2xx_with_matching_subject_passes(self):
        seen_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json={"username": "alice"})

        provider = _provider("https://host.example/validate", self._client(handler))
        result = await provider.authenticate_from_cookies(_cookies())
        assert result.subject == "alice"
        assert "Host-Token=host-session-token" in seen_headers["cookie"]

    async def test_2xx_with_mismatched_subject_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"username": "bob"})

        provider = _provider("https://host.example/validate", self._client(handler))
        with pytest.raises(AuthenticationError, match="does not match"):
            await provider.authenticate_from_cookies(_cookies("alice"))

    async def test_non_2xx_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid session"})

        provider = _provider("https://host.example/validate", self._client(handler))
        with pytest.raises(AuthenticationError, match="validation failed"):
            await provider.authenticate_from_cookies(_cookies())

    async def test_network_error_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = _provider("https://host.example/validate", self._client(handler))
        with pytest.raises(AuthenticationError, match="request failed"):
            await provider.authenticate_from_cookies(_cookies())

    async def test_2xx_without_recognized_subject_field_passes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        provider = _provider("https://host.example/validate", self._client(handler))
        result = await provider.authenticate_from_cookies(_cookies())
        assert result.subject == "alice"

    async def test_non_json_2xx_passes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="OK")

        provider = _provider("https://host.example/validate", self._client(handler))
        result = await provider.authenticate_from_cookies(_cookies())
        assert result.subject == "alice"
