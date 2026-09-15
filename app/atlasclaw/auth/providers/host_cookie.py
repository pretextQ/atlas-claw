# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""HostCookieAuthProvider - authenticate embedded users from host cookies."""

from __future__ import annotations

import logging
from typing import Dict
from urllib.parse import unquote

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.base import AuthProvider

logger = logging.getLogger(__name__)


class HostCookieAuthProvider(AuthProvider):
    """Extract user identity from configurable embedded host cookies."""

    def __init__(
        self,
        *,
        provider_name: str,
        token_cookie_name: str,
        subject_cookie_name: str,
        display_name_cookie_name: str = "",
        user_id_cookie_name: str = "",
        tenant_id_cookie_name: str = "",
    ) -> None:
        self._provider_name = str(provider_name or "host_cookie").strip() or "host_cookie"
        self._token_cookie_name = str(token_cookie_name or "").strip()
        self._subject_cookie_name = str(subject_cookie_name or "").strip()
        self._display_name_cookie_name = str(display_name_cookie_name or "").strip()
        self._user_id_cookie_name = str(user_id_cookie_name or "").strip()
        self._tenant_id_cookie_name = str(tenant_id_cookie_name or "").strip()

    def provider_name(self) -> str:
        return self._provider_name

    async def authenticate(self, credential: str) -> AuthResult:
        """Host cookie auth requires request cookies, not a single credential."""
        raise AuthenticationError(
            "Host cookie provider requires cookies, not a single credential. "
            "Use authenticate_from_cookies() instead."
        )

    async def authenticate_from_cookies(self, cookies: Dict[str, str]) -> AuthResult:
        """Extract user identity from configured host cookies."""
        host_token = cookies.get(self._token_cookie_name, "").strip()
        if not host_token:
            raise AuthenticationError("Missing host authentication cookie")

        if not self._subject_cookie_name:
            raise AuthenticationError("Missing host subject cookie configuration")

        subject = cookies.get(self._subject_cookie_name, "").strip()
        if not subject:
            raise AuthenticationError("Missing host subject cookie")

        raw_display_name = (
            cookies.get(self._display_name_cookie_name, "").strip()
            if self._display_name_cookie_name
            else ""
        )
        display_name = unquote(raw_display_name) if raw_display_name else subject

        user_id = (
            cookies.get(self._user_id_cookie_name, "").strip()
            if self._user_id_cookie_name
            else ""
        )
        tenant_id = (
            cookies.get(self._tenant_id_cookie_name, "").strip()
            if self._tenant_id_cookie_name
            else ""
        ) or "default"

        logger.info(
            "Host cookie auth: provider=%s subject=%s name=%s tenant=%s",
            self._provider_name,
            subject,
            display_name,
            tenant_id,
        )

        return AuthResult(
            subject=subject,
            display_name=display_name,
            tenant_id=tenant_id,
            raw_token=host_token,
            extra={
                "auth_type": "cookie",
                "user_id": user_id,
            },
        )
