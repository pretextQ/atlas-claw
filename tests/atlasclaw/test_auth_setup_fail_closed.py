# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for fail-closed auth middleware installation from config."""

from __future__ import annotations

import pytest

from app.atlasclaw.bootstrap.app_factory_helpers import setup_auth_middleware_from_config


class _FakeConfig:
    def __init__(self, auth, workspace_path: str = ".") -> None:
        self.auth = auth
        self.workspace = type("Workspace", (), {"path": workspace_path})()


class TestAuthSetupFailClosed:
    def test_broken_auth_dict_fails_startup(self, monkeypatch):
        from app.atlasclaw.core import config as config_module

        monkeypatch.setattr(
            config_module,
            "get_config",
            lambda: _FakeConfig(auth={"provider": "local", "jwt": "not-a-model"}),
        )
        with pytest.raises(RuntimeError, match="Invalid auth configuration"):
            setup_auth_middleware_from_config(object())

    def test_config_load_failure_fails_startup(self, monkeypatch):
        from app.atlasclaw.core import config as config_module

        def _boom():
            raise ValueError("bad config file")

        monkeypatch.setattr(config_module, "get_config", _boom)
        with pytest.raises(RuntimeError, match="Failed to load AtlasClaw config"):
            setup_auth_middleware_from_config(object())

    def test_default_jwt_secret_fails_startup(self, monkeypatch):
        """The C1 guard must propagate instead of degrading to anonymous."""
        from app.atlasclaw.auth.config import AuthConfig
        from app.atlasclaw.core import config as config_module

        auth = AuthConfig(provider="local")
        monkeypatch.setattr(config_module, "get_config", lambda: _FakeConfig(auth=auth))
        with pytest.raises(Exception, match="auth.jwt.secret_key"):
            setup_auth_middleware_from_config(object())
