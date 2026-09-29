# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Unit tests for reverse-proxy base-path helpers."""

from __future__ import annotations

from app.atlasclaw.core.base_path import (
    build_base_path_url,
    cookie_path_for_base_path,
    normalize_base_path,
    strip_base_path,
)


class TestBasePathHelpers:
    """Validate browser-visible base-path transformations."""

    def test_normalize_base_path(self) -> None:
        assert normalize_base_path("") == ""
        assert normalize_base_path("/") == ""
        assert normalize_base_path("atlasclaw") == "/atlasclaw"
        assert normalize_base_path("/atlasclaw/") == "/atlasclaw"

    def test_build_base_path_url(self) -> None:
        assert build_base_path_url("", "/api/health") == "/api/health"
        assert build_base_path_url("/atlasclaw", "/api/health") == "/atlasclaw/api/health"
        assert build_base_path_url("/atlasclaw/", "/") == "/atlasclaw/"

    def test_strip_base_path(self) -> None:
        assert strip_base_path("/atlasclaw", "/atlasclaw") == "/"
        assert strip_base_path("/atlasclaw", "/atlasclaw/models") == "/models"
        assert strip_base_path("", "/models") == "/models"

    def test_cookie_path_for_base_path(self) -> None:
        assert cookie_path_for_base_path("") == "/"
        assert cookie_path_for_base_path("/atlasclaw") == "/atlasclaw"


class TestBasePathSanitization:
    """WP-03/F-0054: a hostile base_path must not inject markup."""

    def test_script_breakout_is_stripped(self) -> None:
        # angle brackets and spaces are stripped, slashes preserved
        assert normalize_base_path("/x</script><script>alert(1)</script>") == "/x/scriptscriptalert(1)/script"

    def test_quotes_and_backslashes_are_stripped(self) -> None:
        # double quote and backslash are stripped; apostrophe is a valid URL pchar
        assert normalize_base_path('/a"b\'c\\d') == "/ab'cd"

    def test_angle_brackets_are_stripped_everywhere(self) -> None:
        assert normalize_base_path("/<img src=x>") == "/imgsrc=x"

    def test_safe_characters_are_preserved(self) -> None:
        assert normalize_base_path("/my-app_2/v1.0") == "/my-app_2/v1.0"


class TestScriptSafeJson:
    """WP-03/F-0054: JSON inlined into <script> must not break out."""

    def test_html_specials_are_escaped(self) -> None:
        from app.atlasclaw.bootstrap.app_factory_helpers import _script_safe_json

        encoded = _script_safe_json('/x</script><script>alert(1)</script>')
        assert "<" not in encoded
        assert ">" not in encoded
        assert "&" not in encoded
        import json

        assert json.loads(encoded) == '/x</script><script>alert(1)</script>'

    def test_plain_value_round_trips(self) -> None:
        import json

        from app.atlasclaw.bootstrap.app_factory_helpers import _script_safe_json

        assert json.loads(_script_safe_json('/atlasclaw')) == '/atlasclaw'
