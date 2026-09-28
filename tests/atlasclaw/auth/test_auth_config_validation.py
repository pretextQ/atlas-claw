# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the JWT secret fail-closed guard in auth config validation."""

from __future__ import annotations

import pytest

from app.atlasclaw.auth.config import AuthConfig


class TestJwtSecretValidation:
    def test_local_provider_with_default_secret_is_rejected(self):
        config = AuthConfig(provider="local")
        with pytest.raises(ValueError, match="auth.jwt.secret_key"):
            config.validate_provider_config()

    def test_local_provider_with_unresolved_env_placeholder_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("ATLASCLAW_JWT_SECRET", raising=False)
        config = AuthConfig(
            provider="local",
            jwt={"secret_key": "${ATLASCLAW_JWT_SECRET}"},
        )
        with pytest.raises(ValueError, match="auth.jwt.secret_key"):
            config.validate_provider_config()

    def test_local_provider_with_explicit_secret_passes(self):
        config = AuthConfig(provider="local", jwt={"secret_key": "strong-test-secret"})
        config.validate_provider_config()

    def test_none_provider_allows_default_secret(self):
        config = AuthConfig(provider="none")
        config.validate_provider_config()

    def test_disabled_auth_allows_default_secret(self):
        config = AuthConfig(provider="local", enabled=False)
        config.validate_provider_config()

    def test_host_cookie_provider_with_default_secret_is_rejected(self):
        config = AuthConfig(provider="host_cookie")
        with pytest.raises(ValueError, match="auth.jwt.secret_key"):
            config.validate_provider_config()
