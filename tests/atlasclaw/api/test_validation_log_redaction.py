# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for request-validation log redaction."""

from __future__ import annotations

from app.atlasclaw.api.routes import _safe_decode_request_body


class TestSafeDecodeRequestBody:
    def test_password_field_is_redacted(self):
        body = b'{"username": "alice", "password": "super-secret"}'
        rendered = _safe_decode_request_body(body)
        assert "super-secret" not in rendered
        assert "alice" in rendered

    def test_nested_secret_fields_are_redacted(self):
        body = b'{"user": {"password": "pw", "api_key": "key123"}, "name": "x"}'
        rendered = _safe_decode_request_body(body)
        assert "pw" not in rendered
        assert "key123" not in rendered

    def test_non_json_body_is_not_logged_verbatim(self):
        rendered = _safe_decode_request_body(b"password=hunter2&username=a")
        assert "hunter2" not in rendered
        assert "non-json body" in rendered

    def test_empty_body(self):
        assert _safe_decode_request_body(b"") == "<empty>"

    def test_long_values_are_truncated(self):
        body = b'{"description": "' + b"a" * 5000 + b'"}'
        rendered = _safe_decode_request_body(body)
        assert len(rendered) <= 1100
