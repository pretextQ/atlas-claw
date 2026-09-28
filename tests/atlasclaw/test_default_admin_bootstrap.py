# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the default local admin bootstrap password policy."""

from __future__ import annotations

from app.atlasclaw.bootstrap.startup_helpers import _resolve_bootstrap_admin_password


class TestResolveBootstrapAdminPassword:
    def test_explicit_configured_password_is_kept(self):
        password, generated = _resolve_bootstrap_admin_password("Admin@123")
        assert password == "Admin@123"
        assert generated is False

    def test_insecure_default_password_is_regenerated(self):
        password, generated = _resolve_bootstrap_admin_password("admin")
        assert password != "admin"
        assert generated is True

    def test_empty_password_is_regenerated(self):
        password, generated = _resolve_bootstrap_admin_password("")
        assert password
        assert generated is True

    def test_whitespace_password_is_regenerated(self):
        password, generated = _resolve_bootstrap_admin_password("   ")
        assert password
        assert generated is True

    def test_generated_password_is_random_and_non_default(self):
        first, first_generated = _resolve_bootstrap_admin_password("admin")
        second, _ = _resolve_bootstrap_admin_password("admin")
        assert first_generated is True
        assert first != second
        assert first != "admin"
