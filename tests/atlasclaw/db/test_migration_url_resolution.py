# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for migration URL resolution fail-fast behavior."""

from __future__ import annotations

import pytest

from app.atlasclaw.core.config_schema import DatabaseConfig
from app.atlasclaw.db.database import resolve_configured_database_url


class TestResolveConfiguredUrl:
    def test_sqlite_dict_config_resolves(self):
        url = resolve_configured_database_url(
            {"type": "sqlite", "sqlite": {"path": "./x.db"}}
        )
        assert url == "sqlite+aiosqlite:///./x.db"

    def test_mysql_dict_config_resolves(self):
        url = resolve_configured_database_url(
            {
                "type": "mysql",
                "mysql": {
                    "host": "db",
                    "port": 3306,
                    "user": "u",
                    "password": "p",
                    "database": "app",
                },
            }
        )
        assert url.startswith("mysql+aiomysql://u:p@db:3306/app")

    def test_unknown_type_raises_instead_of_falling_back(self):
        with pytest.raises(ValueError, match="Unsupported database type"):
            resolve_configured_database_url({"type": "oracle"})

    def test_mysql_pydantic_config_resolves(self):
        config = DatabaseConfig(
            type="mysql",
            mysql={
                "host": "db",
                "port": 3306,
                "user": "u",
                "password": "p",
                "database": "app",
            },
        )
        url = resolve_configured_database_url(config)
        assert url.startswith("mysql+aiomysql://u:p@db:3306/app")

    def test_mysql_without_section_raises(self):
        config = DatabaseConfig(type="mysql")
        with pytest.raises(ValueError, match="MySQL config section is missing"):
            resolve_configured_database_url(config)
