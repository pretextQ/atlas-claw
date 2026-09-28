# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for provider-scoped request cookie filtering."""

from __future__ import annotations

from app.atlasclaw.api.deps_context import _extract_provider_request_cookies


class TestExtractProviderRequestCookies:
    def test_atlasclaw_owned_cookies_are_dropped(self):
        filtered = _extract_provider_request_cookies(
            {
                "AtlasClaw-Authenticate": "jwt-value",
                "atlasclaw_session": "session-value",
                "sso_state": "state-value",
                "oidc_id_token": "id-token",
            }
        )
        assert filtered == {}

    def test_external_host_cookies_are_kept(self):
        filtered = _extract_provider_request_cookies(
            {
                "AtlasClaw-Authenticate": "jwt-value",
                "CloudChef-Authenticate": "host-token",
                "userLoginId": "alice",
            }
        )
        assert filtered == {
            "CloudChef-Authenticate": "host-token",
            "userLoginId": "alice",
        }

    def test_empty_and_blank_values_are_dropped(self):
        filtered = _extract_provider_request_cookies(
            {"CloudChef-Authenticate": "", "userLoginId": "alice"}
        )
        assert filtered == {"userLoginId": "alice"}

    def test_non_dict_input_returns_empty(self):
        assert _extract_provider_request_cookies(None) == {}
