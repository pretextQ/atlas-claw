# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""HostCookieAuthProvider - authenticate embedded users from host cookies."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote

import httpx

from app.atlasclaw.auth.models import AuthResult, AuthenticationError
from app.atlasclaw.auth.providers.base import AuthProvider

logger = logging.getLogger(__name__)


class HostCookieAuthProvider(AuthProvider):
    """Extract user identity from configurable embedded host cookies.

    When ``validate_url`` is configured, every login attempt is re-checked
    against the host application: the request cookies are forwarded to that
    endpoint and the identity is only accepted when the host answers 2xx and
    any subject it returns matches the subject cookie. Without it, cookies
    are trusted as-is, so only use that mode inside a network where clients
    cannot set the host cookies themselves.
    """

    _SUBJECT_RESPONSE_KEYS = {"username", "userloginid", "subject", "loginname", "account"}

    def __init__(
        self,
        *,
        provider_name: str,
        token_cookie_name: str,
        subject_cookie_name: str,
        display_name_cookie_name: str = "",
        user_id_cookie_name: str = "",
        tenant_id_cookie_name: str = "",
        validate_url: str = "",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._provider_name = str(provider_name or "host_cookie").strip() or "host_cookie"
        self._token_cookie_name = str(token_cookie_name or "").strip()
        self._subject_cookie_name = str(subject_cookie_name or "").strip()
        self._display_name_cookie_name = str(display_name_cookie_name or "").strip()
        self._user_id_cookie_name = str(user_id_cookie_name or "").strip()
        self._tenant_id_cookie_name = str(tenant_id_cookie_name or "").strip()
        self._validate_url = str(validate_url or "").strip()
        self._http_client = http_client
        if not self._validate_url:
            logger.warning(
                "HostCookieAuthProvider has no validate_url configured; host cookies "
                "are trusted as-is and any client able to set them can impersonate "
                "any subject. Configure auth.host_cookie.validate_url for production."
            )

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

        await self._validate_host_token(cookies, subject)

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

    async def _validate_host_token(self, cookies: Dict[str, str], subject: str) -> None:
        """Re-check the host token against the configured host endpoint."""
        if not self._validate_url:
            return

        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        try:
            if self._http_client is not None:
                response = await self._http_client.get(
                    self._validate_url,
                    headers={"Cookie": cookie_header},
                )
            else:
                async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                    response = await client.get(
                        self._validate_url,
                        headers={"Cookie": cookie_header},
                    )
        except httpx.HTTPError as exc:
            logger.warning("Host cookie validation request failed: %s", exc)
            raise AuthenticationError("Host cookie validation request failed") from exc

        if not (200 <= response.status_code < 300):
            logger.warning(
                "Host cookie validation rejected: status=%s url=%s",
                response.status_code,
                self._validate_url,
            )
            raise AuthenticationError("Host cookie validation failed")

        remote_subject = self._extract_remote_subject(self._safe_json(response))
        if remote_subject and remote_subject != subject:
            logger.warning(
                "Host cookie validation subject mismatch: cookie=%s host=%s",
                subject,
                remote_subject,
            )
            raise AuthenticationError("Host cookie identity does not match the host session")

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    @classmethod
    def _extract_remote_subject(cls, payload: Any) -> str:
        """Find the host-reported subject in a validation response body."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                if str(key).strip().lower() in cls._SUBJECT_RESPONSE_KEYS:
                    if isinstance(value, (str, int)):
                        text = str(value).strip()
                        if text:
                            return text
            for value in payload.values():
                found = cls._extract_remote_subject(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = cls._extract_remote_subject(item)
                if found:
                    return found
        return ""
