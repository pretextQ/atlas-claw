# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
AuthStrategy 单元测试

涵盖：TTL 缓存命中、缓存未命中完整认证流程。
"""

from __future__ import annotations

import json
import pytest

from app.atlasclaw.auth.models import AuthResult
from app.atlasclaw.auth.strategy import AuthStrategy
from app.atlasclaw.auth.providers.base import AuthProvider
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.user import UserService


class _MockProvider(AuthProvider):
    def __init__(self, subject: str = "mock-user", call_count: list | None = None):
        self._subject = subject
        self.call_count = call_count if call_count is not None else []

    def provider_name(self) -> str:
        return "local"

    async def authenticate(self, credential: str) -> AuthResult:
        self.call_count.append(credential)
        return AuthResult(subject=self._subject, display_name="Mock", raw_token=credential)


class TestAuthStrategy:

    @pytest.mark.asyncio
    async def test_full_auth_flow_creates_user_info(self, tmp_path):
        provider = _MockProvider(subject="alice")
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=60,
        )

        user_info = await strategy.resolve_user("token-alice")

        assert user_info.user_id == "alice"
        assert user_info.display_name == "Mock"
        assert user_info.raw_token == "token-alice"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_provider(self, tmp_path):
        calls: list[str] = []
        provider = _MockProvider(subject="bob", call_count=calls)
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=60,
        )


        await strategy.resolve_user("token-bob")
        await strategy.resolve_user("token-bob")

        # Provider should have been called only once
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_after_ttl_expiry(self, tmp_path):
        calls: list[str] = []
        provider = _MockProvider(subject="carol", call_count=calls)
        # TTL = 0 → every call is a cache miss
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=0,
        )

        await strategy.resolve_user("token-carol")
        await strategy.resolve_user("token-carol")

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_different_tokens_are_cached_independently(self, tmp_path):
        calls: list[str] = []
        provider = _MockProvider(subject="dave", call_count=calls)
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=60,
        )


        await strategy.resolve_user("token-A")
        await strategy.resolve_user("token-B")
        await strategy.resolve_user("token-A")  # cache hit

        assert len(calls) == 2  # token-A and token-B each called once

    @pytest.mark.asyncio
    async def test_login_creates_user_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        provider = _MockProvider(subject="eve")
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(workspace),
            cache_ttl_seconds=60,
        )

        user_info = await strategy.resolve_user("token-eve")
        user_dir = workspace / "users" / user_info.user_id
        user_config = user_dir / "user_setting.json"

        assert (user_dir / "sessions").exists()
        assert (user_dir / "memory").exists()
        assert user_config.exists()
        with open(user_config, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["channels"] == {}
        assert config["preferences"] == {}
        assert config["providers"] == {}

    @pytest.mark.asyncio
    async def test_resolve_user_from_auth_result_creates_db_backed_user(self, tmp_path):
        manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "users.db"))
        )
        await manager.create_tables()

        workspace = tmp_path / "workspace"
        strategy = AuthStrategy(
            providers=[],
            workspace_path=str(workspace),
            cache_ttl_seconds=60,
        )

        user_info = await strategy.resolve_user_from_auth_result(
            provider="host_cookie",
            result=AuthResult(
                subject="cmp_user",
                display_name="CMP User",
                raw_token="cookie-token",
                extra={"auth_type": "cookie"},
            ),
            raw_token="cookie-token",
            auth_type="cookie",
        )

        assert user_info.user_id
        assert user_info.user_id != "cmp_user"
        assert user_info.display_name == "CMP User"
        assert user_info.roles == ["user"]
        assert (workspace / "users" / user_info.user_id / "user_setting.json").exists()

        async with manager.get_session() as session:
            user = await UserService.get_by_username(session, "cmp_user")
            assert user is not None
            assert user.id == user_info.user_id
            assert user.password is None

        await manager.close()


class _DbBackedProvider(AuthProvider):
    """Provider whose results carry a database user id (as the local provider does)."""

    def __init__(self, user_id: str, call_count: list | None = None):
        self._user_id = user_id
        self.call_count = call_count if call_count is not None else []

    def provider_name(self) -> str:
        return "oidc_jwt"

    async def authenticate(self, credential: str) -> AuthResult:
        self.call_count.append(credential)
        return AuthResult(
            subject=self._user_id,
            display_name="Db User",
            raw_token=credential,
            extra={"db_user_id": self._user_id, "auth_type": "oidc"},
        )


class TestAuthCacheInvalidation:
    """Cached credentials must not outlive an account deactivation."""

    async def _seed_user(self, tmp_path, *, username: str, is_active: bool) -> str:
        manager = await init_database(
            DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "auth.db"))
        )
        await manager.create_tables()
        from app.atlasclaw.db.database import get_db_manager
        from app.atlasclaw.db.schemas import UserCreate

        async with get_db_manager().get_session() as session:
            user = await UserService.create(
                session,
                UserCreate(
                    username=username,
                    display_name=username,
                    auth_type="oidc",
                    is_active=is_active,
                ),
            )
            return user.id

    @pytest.mark.asyncio
    async def test_deactivated_user_is_rejected_on_cache_hit(self, tmp_path):
        user_id = await self._seed_user(tmp_path, username="cached-user", is_active=True)
        calls: list[str] = []
        provider = _DbBackedProvider(user_id=user_id, call_count=calls)
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=600,
        )

        first = await strategy.resolve_user("token-1")
        # The cached identity carries the database user id that revalidation
        # checks against.
        assert first.extra["db_user_id"] == user_id
        assert calls == ["token-1"]

        # The account is deactivated while the credential is still cached.
        from app.atlasclaw.db.database import get_db_manager
        from app.atlasclaw.db.schemas import UserUpdate

        async with get_db_manager().get_session() as session:
            await UserService.update(session, user_id, UserUpdate(is_active=False))

        with pytest.raises(Exception) as excinfo:
            await strategy.resolve_user("token-1")
        assert "inactive" in str(excinfo.value).lower()
        # The stale entry is gone, so a retry re-authenticates.
        assert "token-1" not in strategy._cache

    @pytest.mark.asyncio
    async def test_active_user_keeps_serving_from_cache(self, tmp_path):
        user_id = await self._seed_user(tmp_path, username="active-user", is_active=True)
        calls: list[str] = []
        provider = _DbBackedProvider(user_id=user_id, call_count=calls)
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=600,
        )

        await strategy.resolve_user("token-2")
        await strategy.resolve_user("token-2")

        # One authentication only: the second call was served from the cache.
        assert calls == ["token-2"]

    @pytest.mark.asyncio
    async def test_cache_is_bounded(self, tmp_path):
        provider = _MockProvider(subject="bounded-user")
        strategy = AuthStrategy(
            providers=[provider],
            workspace_path=str(tmp_path),
            cache_ttl_seconds=600,
            cache_max_entries=3,
        )

        for index in range(6):
            await strategy.resolve_user(f"token-{index}")

        assert len(strategy._cache) == 3
        # Least recently used credentials were evicted first.
        assert "token-0" not in strategy._cache
        assert "token-5" in strategy._cache
