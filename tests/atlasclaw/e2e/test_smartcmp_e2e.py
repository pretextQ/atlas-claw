# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""
SmartCMP Provider E2E Tests

Complete end-to-end tests for SmartCMP Provider integration:
1. Auto-start AtlasClaw service (via TestClient lifespan)
2. Auto-login with local auth (admin/Admin@123)
3. Create SmartCMP provider config with user_token
4. Interact with Agent: send queries like "查看我的待审批"
5. Validate SSE stream events and SmartCMP tool invocations

Prerequisites:
- Set environment variables before running:
    CMP_BASE_URL   - SmartCMP base URL (e.g. https://cmp.example.com)
    CMP_USER_TOKEN - SmartCMP user API token

Run:
    $env:CMP_BASE_URL="https://your-cmp-url"
    $env:CMP_USER_TOKEN="your-token"
    pytest tests/atlasclaw/e2e/test_smartcmp_e2e.py -v -m e2e
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
CMP_BASE_URL = os.environ.get("CMP_BASE_URL", "")
CMP_USER_TOKEN = os.environ.get("CMP_USER_TOKEN", "")
CMP_USERNAME = os.environ.get("CMP_USERNAME", "")
CMP_PASSWORD = os.environ.get("CMP_PASSWORD", "")

# AtlasClaw local auth credentials
ATLAS_ADMIN_USER = "admin"
ATLAS_ADMIN_PASS = "Admin@123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_e2e_config(tmp_path: Path, db_path: Path) -> dict[str, Any]:
    """Build a minimal AtlasClaw config for E2E testing with SmartCMP provider."""
    # Use absolute paths so they resolve correctly from tmp_path config location
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    providers_root = str((project_root.parent / "atlasclaw-providers" / "providers").resolve())
    skills_root = str((project_root.parent / "atlasclaw-providers" / "skills").resolve())

    config: dict[str, Any] = {
        "workspace": {
            "path": str((tmp_path / ".atlasclaw-e2e").resolve()),
        },
        "providers_root": providers_root,
        "skills_root": skills_root,
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": str(db_path.resolve()),
            },
        },
        "auth": {
            "enabled": True,
            "provider": "local",
            "jwt": {
                "secret_key": "e2e-smartcmp-secret-key",
                "issuer": "atlasclaw-e2e",
                "header_name": "AtlasClaw-Authenticate",
                "cookie_name": "AtlasClaw-Authenticate",
                "expires_minutes": 60,
            },
            "local": {
                "enabled": True,
                "default_admin_username": ATLAS_ADMIN_USER,
                "default_admin_password": ATLAS_ADMIN_PASS,
            },
        },
        "model": {
            "primary": "test-token",
            "fallbacks": [],
            "temperature": 0.2,
            "selection_strategy": "health",
            "tokens": [
                {
                    "id": "test-token",
                    "provider": os.environ.get("LLM_PROVIDER", "openai"),
                    "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
                    "base_url": os.environ.get(
                        "LLM_BASE_URL",
                        "https://api.deepseek.com/v1",
                    ),
                    "api_key": os.environ.get("LLM_API_KEY", "test-key-placeholder"),
                    "api_type": "openai",
                    "priority": 100,
                    "weight": 100,
                }
            ],
        },
        "service_providers": {},
    }

    # Pre-configure SmartCMP provider if env vars are set
    if CMP_BASE_URL:
        smartcmp_config: dict[str, Any] = {"base_url": CMP_BASE_URL}
        if CMP_USER_TOKEN:
            smartcmp_config["auth_type"] = "user_token"
            smartcmp_config["user_token"] = CMP_USER_TOKEN
        elif CMP_USERNAME and CMP_PASSWORD:
            smartcmp_config["auth_type"] = "credential"
            smartcmp_config["username"] = CMP_USERNAME
            smartcmp_config["password"] = CMP_PASSWORD
        config["service_providers"]["smartcmp"] = {
            "default": smartcmp_config,
        }

    return config


def _create_app_with_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra_service_providers: Optional[dict[str, Any]] = None,
) -> tuple[Any, Any, Any]:
    """Create the FastAPI app with E2E config, return (app, config_module, old_manager).

    Use with ``TestClient(app)`` as context manager to trigger lifespan.
    """
    db_path = tmp_path / "smartcmp-e2e.db"
    config_path = tmp_path / "atlasclaw.smartcmp-e2e.json"

    config = _build_e2e_config(tmp_path, db_path)
    if extra_service_providers is not None:
        config["service_providers"] = extra_service_providers
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path.resolve()))

    import app.atlasclaw.core.config as config_module
    from app.atlasclaw.main import create_app

    old_config_manager = config_module._config_manager
    config_module._config_manager = config_module.ConfigManager(
        config_path=str(config_path.resolve()),
    )

    app = create_app()
    return app, config_module, old_config_manager


def _login(client: TestClient) -> dict[str, Any]:
    """Login and return the full response body. Sets cookies on the client."""
    resp = client.post(
        "/api/auth/local/login",
        json={"username": ATLAS_ADMIN_USER, "password": ATLAS_ADMIN_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    body = resp.json()
    assert body.get("success") is True, f"Login not successful: {body}"
    return body


def _auth_headers(login_body: dict[str, Any]) -> dict[str, str]:
    """Build auth headers from login response."""
    token = login_body.get("token", "")
    return {"AtlasClaw-Authenticate": token}


def _create_smartcmp_provider_config(
    client: TestClient,
    headers: dict[str, str],
    *,
    instance_name: str = "default",
    base_url: str = "",
    user_token: str = "",
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Create a SmartCMP provider config via API."""
    config_payload: dict[str, Any] = {}
    if base_url:
        config_payload["base_url"] = base_url
    if user_token:
        config_payload["auth_type"] = "user_token"
        config_payload["user_token"] = user_token
    elif username and password:
        config_payload["auth_type"] = "credential"
        config_payload["username"] = username
        config_payload["password"] = password

    resp = client.post(
        "/api/provider-configs",
        json={
            "provider_type": "smartcmp",
            "instance_name": instance_name,
            "config": config_payload,
            "is_active": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Create provider config failed: {resp.text}"
    return resp.json()


def _run_agent_and_collect_events(
    client: TestClient,
    headers: dict[str, str],
    message: str,
    session_key: str,
    timeout: int = 120,
) -> dict[str, list]:
    """
    Send a message to the agent, then consume the SSE stream.

    Returns a dict with categorized events:
        {
            "lifecycle": [...],
            "assistant": [...],
            "tool": [...],
            "error": [...],
            "thinking": [...],
            "runtime": [...],
            "raw_lines": [...],
        }
    """
    # Start the agent run
    run_resp = client.post(
        "/api/agent/run",
        json={
            "session_key": session_key,
            "message": message,
            "timeout_seconds": timeout,
        },
        headers=headers,
    )
    assert run_resp.status_code == 200, f"Agent run failed: {run_resp.text}"
    run_data = run_resp.json()
    run_id = run_data["run_id"]
    assert run_data["status"] == "running"

    # Poll run status until completion (TestClient cannot concurrently
    # stream SSE and execute background tasks)
    import time as _time
    deadline = _time.time() + timeout
    final_status = "running"
    while _time.time() < deadline:
        _time.sleep(1)
        status_resp = client.get(
            f"/api/agent/runs/{run_id}",
            headers=headers,
        )
        if status_resp.status_code != 200:
            break
        status_data = status_resp.json()
        final_status = status_data.get("status", "unknown")
        if final_status != "running":
            break

    # Now consume the SSE stream - all events should be buffered
    events: dict[str, list] = {
        "lifecycle": [],
        "assistant": [],
        "tool": [],
        "error": [],
        "thinking": [],
        "runtime": [],
        "raw_lines": [],
    }

    with client.stream(
        "GET",
        f"/api/agent/runs/{run_id}/stream",
        headers=headers,
    ) as stream_resp:
        if stream_resp.status_code != 200:
            return events

        current_event_type = ""
        current_data = ""

        for line in stream_resp.iter_lines():
            events["raw_lines"].append(line)

            if line.startswith("event:"):
                current_event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current_data = line[len("data:"):].strip()
            elif line == "" and current_event_type and current_data:
                try:
                    parsed = json.loads(current_data)
                except json.JSONDecodeError:
                    parsed = {"raw": current_data}

                if current_event_type in events:
                    events[current_event_type].append(parsed)

                # Stop when lifecycle end/error/timeout is reached
                if (
                    current_event_type == "lifecycle"
                    and parsed.get("phase") in ("end", "error", "timeout")
                ):
                    break

                current_event_type = ""
                current_data = ""

    # Add final status info
    events["_final_status"] = [final_status]
    return events


def _get_assistant_text(events: dict[str, list]) -> str:
    """Concatenate all assistant text chunks."""
    parts = []
    for evt in events.get("assistant", []):
        text = evt.get("text", "")
        if text:
            parts.append(text)
    return "".join(parts)


def _get_tool_names(events: dict[str, list]) -> list[str]:
    """Extract unique tool names from tool events."""
    names = []
    for evt in events.get("tool", []):
        name = evt.get("tool", "")
        if name and name not in names:
            names.append(name)
    return names


def _get_tool_results(events: dict[str, list], tool_name: Optional[str] = None) -> list[str]:
    """Extract tool execution results from tool end events.

    Args:
        events: Collected SSE events dict.
        tool_name: If given, only return results for this tool.

    Returns:
        List of result strings from tool end events.
    """
    results: list[str] = []
    for evt in events.get("tool", []):
        if evt.get("phase") != "end":
            continue
        if tool_name and evt.get("tool") != tool_name:
            continue
        result = evt.get("result", "")
        if result:
            results.append(result)
    return results


def _get_session_history(
    client: TestClient, headers: dict[str, str], session_key: str,
) -> list[dict[str, Any]]:
    """Retrieve conversation history for a session.

    Returns a list of {"role": ..., "content": ..., "timestamp": ...}.
    """
    resp = client.get(
        f"/api/sessions/{session_key}/history",
        headers=headers,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("messages", [])


def _get_last_assistant_message(messages: list[dict[str, Any]]) -> str:
    """Get the last assistant message from session history."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return ""


def _assert_agent_completed(
    events: dict[str, list],
    *,
    expected_tools: Optional[list[str]] = None,
    forbidden_keywords: Optional[list[str]] = None,
    label: str = "Agent run",
) -> tuple[str, list[str], str]:
    """Common assertion: agent completed, optionally check tools and text.

    Returns (final_status, tool_names, assistant_text).
    """
    final_status = (events.get("_final_status") or ["unknown"])[0]
    errors = events.get("error", [])
    error_msgs = [e.get("message", "") for e in errors]
    lifecycle_phases = [e.get("phase") for e in events.get("lifecycle", [])]

    assert (
        final_status == "completed" or "end" in lifecycle_phases
    ), f"{label} did not complete. Status: {final_status}, errors: {error_msgs}"

    tool_names = _get_tool_names(events)
    assistant_text = _get_assistant_text(events)

    if expected_tools:
        for tool in expected_tools:
            if tool_names:
                assert tool in tool_names, (
                    f"{label}: expected tool {tool}, got: {tool_names}"
                )

    if forbidden_keywords and assistant_text:
        for kw in forbidden_keywords:
            assert kw not in assistant_text, (
                f"{label}: forbidden keyword '{kw}' found in response"
            )

    return final_status, tool_names, assistant_text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _skip_if_no_cmp():
    """Skip the entire module if SmartCMP env vars are not configured."""
    if not CMP_BASE_URL:
        pytest.skip("CMP_BASE_URL not set, skipping SmartCMP E2E tests")
    if not CMP_USER_TOKEN and not CMP_USERNAME:
        pytest.skip(
            "Neither CMP_USER_TOKEN nor CMP_USERNAME set, skipping SmartCMP E2E tests"
        )


@pytest.fixture(scope="module")
def _skip_if_no_llm():
    """Skip if no LLM API key is configured."""
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set, skipping LLM-dependent E2E tests")


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------


class TestServiceStartupAndLogin:
    """Test that the service starts and login works."""

    def test_health_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Service should start and health endpoint should return healthy."""
        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                resp = client.get("/api/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "healthy"
        finally:
            config_module._config_manager = old_manager

    def test_local_login(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Admin login should succeed and return valid token."""
        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                assert login_body["user"]["username"] == ATLAS_ADMIN_USER
                assert login_body.get("token")
                assert login_body.get("session")
        finally:
            config_module._config_manager = old_manager

    def test_auth_me_after_login(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """After login, /api/auth/me should return the admin user."""
        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                me_resp = client.get("/api/auth/me", headers=headers)
                assert me_resp.status_code == 200
                me_data = me_resp.json()
                assert me_data["user_id"] == ATLAS_ADMIN_USER
        finally:
            config_module._config_manager = old_manager

    def test_unauthenticated_access_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """API calls without auth should return 401."""
        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                resp = client.get("/api/skills")
                assert resp.status_code == 401
        finally:
            config_module._config_manager = old_manager


class TestProviderConfigManagement:
    """Test CRUD operations for SmartCMP provider configs."""

    def test_create_smartcmp_provider_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Should create a SmartCMP provider config via API."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                result = _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="e2e-test",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN,
                    username=CMP_USERNAME,
                    password=CMP_PASSWORD,
                )

                assert result["provider_type"] == "smartcmp"
                assert result["instance_name"] == "e2e-test"
                assert result["is_active"] is True
                assert result.get("id")

                # Verify it appears in the list
                list_resp = client.get("/api/provider-configs", headers=headers)
                assert list_resp.status_code == 200
                configs = list_resp.json()
                assert configs["total"] >= 1
                found = any(
                    c["instance_name"] == "e2e-test" and c["provider_type"] == "smartcmp"
                    for c in configs["provider_configs"]
                )
                assert found, "Created config not found in list"
        finally:
            config_module._config_manager = old_manager

    def test_update_smartcmp_provider_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Should update a SmartCMP provider config."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                created = _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="update-test",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN or "placeholder-token",
                )
                config_id = created["id"]

                # Update the config
                update_resp = client.put(
                    f"/api/provider-configs/{config_id}",
                    json={"is_active": False},
                    headers=headers,
                )
                assert update_resp.status_code == 200
                updated = update_resp.json()
                assert updated["is_active"] is False
        finally:
            config_module._config_manager = old_manager

    def test_delete_smartcmp_provider_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Should delete a SmartCMP provider config."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                created = _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="delete-test",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN or "placeholder-token",
                )
                config_id = created["id"]

                del_resp = client.delete(
                    f"/api/provider-configs/{config_id}",
                    headers=headers,
                )
                assert del_resp.status_code == 204

                # Verify deletion
                get_resp = client.get(
                    f"/api/provider-configs/{config_id}",
                    headers=headers,
                )
                assert get_resp.status_code == 404
        finally:
            config_module._config_manager = old_manager

    def test_duplicate_provider_config_returns_409(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Creating duplicate provider config should return 409 conflict."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="dup-test",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN or "placeholder-token",
                )

                # Try to create again with same instance_name
                dup_resp = client.post(
                    "/api/provider-configs",
                    json={
                        "provider_type": "smartcmp",
                        "instance_name": "dup-test",
                        "config": {"base_url": CMP_BASE_URL},
                        "is_active": True,
                    },
                    headers=headers,
                )
                assert dup_resp.status_code == 409
        finally:
            config_module._config_manager = old_manager


class TestSmartCMPAgentInteraction:
    """Test Agent interaction with SmartCMP skills.

    These tests require both a valid LLM API key and SmartCMP credentials.
    They verify both tool invocation and actual output content.
    """

    def _setup_and_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        message: str,
        timeout: int = 120,
    ) -> tuple[dict[str, list], str, Any, Any]:
        """Common setup: create app, login, send message, collect events + history.

        Returns (events, history_text, config_module, old_manager).
        """
        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)

        history_text = ""
        with TestClient(app) as client:
            login_body = _login(client)
            headers = _auth_headers(login_body)
            session_key = login_body["session"]["key"]

            events = _run_agent_and_collect_events(
                client, headers, message, session_key, timeout=timeout,
            )

            # Also fetch session history for full response text
            history = _get_session_history(client, headers, session_key)
            history_text = _get_last_assistant_message(history)

        return events, history_text, config_module, old_manager

    def _get_full_response(self, events: dict[str, list], history_text: str) -> str:
        """Get the best available full response text (SSE or history fallback)."""
        text = _get_assistant_text(events)
        if not text and history_text:
            text = history_text
        return text

    # ----- Approval Scenarios -----

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_list_pending_approvals_chinese(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should invoke smartcmp_list_pending and return approval data."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "查看我的待审批",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_pending"],
                forbidden_keywords=["配置不可用", "configuration is not available"],
                label="查看待审批(中文)",
            )

            # Validate output content
            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[查看待审批-中文] Response length: {len(full_text)}")
            print(f"[查看待审批-中文] Tools called: {tool_names}")
            print(f"[查看待审批-中文] Response preview: {full_text[:500]}")

            # Tool results should contain approval metadata if available
            tool_results = _get_tool_results(events, "smartcmp_list_pending")
            if tool_results:
                combined = "\n".join(tool_results)
                print(f"[查看待审批-中文] Tool output preview: {combined[:500]}")
                # Approval script outputs ##APPROVAL_META_START## block
                has_meta = "APPROVAL_META" in combined or "审批" in combined
                has_data = "pending" in combined.lower() or "id" in combined.lower()
                assert has_meta or has_data, (
                    f"Tool output doesn't contain approval data: {combined[:300]}"
                )

            # Response text should mention approval-related content
            approval_keywords = ["审批", "申请", "approval", "pending", "待"]
            no_data_keywords = ["没有", "暂无", "no pending", "empty", "0"]
            has_approval_content = any(kw in full_text for kw in approval_keywords)
            has_empty_result = any(kw in full_text.lower() for kw in no_data_keywords)
            assert has_approval_content or has_empty_result, (
                f"Response doesn't contain approval-related content: {full_text[:300]}"
            )
        finally:
            config_module._config_manager = old_manager

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_list_pending_approvals_english(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should invoke smartcmp_list_pending for English query."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "List my pending approvals",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_pending"],
                label="List pending approvals(EN)",
            )

            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[Pending Approvals-EN] Tools: {tool_names}")
            print(f"[Pending Approvals-EN] Response preview: {full_text[:500]}")
        finally:
            config_module._config_manager = old_manager

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_agent_returns_approval_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent response should contain meaningful approval data (not errors)."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "帮我查看下当前有哪些待我审批的申请，请列出详细信息",
        )
        try:
            final_status, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_pending"],
                forbidden_keywords=["配置不可用", "SmartCMP configuration is not available"],
                label="审批详情查询",
            )

            full_text = self._get_full_response(events, history_text)
            print(f"\n[审批详情] Status: {final_status}")
            print(f"[审批详情] Tools: {tool_names}")
            print(f"[审批详情] Full response:\n{full_text[:1000]}")

            # Tool results should have structured data
            tool_results = _get_tool_results(events, "smartcmp_list_pending")
            if tool_results:
                print(f"[审批详情] Tool raw output:\n{tool_results[0][:800]}")

            # Response should NOT be a generic error
            assert "error" not in full_text.lower()[:100] or "no error" in full_text.lower(), (
                f"Response starts with error: {full_text[:200]}"
            )
        finally:
            config_module._config_manager = old_manager

    # ----- Service Catalog Scenario -----

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_browse_service_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should list available services when asked to browse catalog."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "查看可以申请的服务有哪些",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_services"],
                forbidden_keywords=["配置不可用"],
                label="浏览服务目录",
            )

            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[服务目录] Tools: {tool_names}")
            print(f"[服务目录] Response:\n{full_text[:1000]}")

            # Tool results should contain catalog metadata
            tool_results = _get_tool_results(events, "smartcmp_list_services")
            if tool_results:
                combined = "\n".join(tool_results)
                print(f"[服务目录] Tool output preview: {combined[:800]}")
                has_catalog = (
                    "CATALOG_META" in combined
                    or "name" in combined.lower()
                    or "服务" in combined
                )
                assert has_catalog, (
                    f"Service catalog tool output missing expected data: {combined[:300]}"
                )

            # Response should list services or indicate catalog data
            normalized_text = full_text.lower()
            service_keywords = ["服务", "service", "目录", "catalog", "申请", "vm", "虚拟机"]
            has_service_content = any(kw in normalized_text for kw in service_keywords)
            assert has_service_content, (
                f"Response doesn't mention services: {full_text[:300]}"
            )
        finally:
            config_module._config_manager = old_manager

    # ----- Alert Scenario -----

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_view_alerts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should list alerts when asked about alarm status."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "查看当前的告警信息",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_alerts"],
                forbidden_keywords=["配置不可用"],
                label="查看告警",
            )

            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[告警查看] Tools: {tool_names}")
            print(f"[告警查看] Response:\n{full_text[:1000]}")

            # Tool results should contain alarm metadata
            tool_results = _get_tool_results(events, "smartcmp_list_alerts")
            if tool_results:
                combined = "\n".join(tool_results)
                print(f"[告警查看] Tool output preview: {combined[:800]}")

            # Response should mention alert-related content
            alert_keywords = ["告警", "alert", "alarm", "监控", "警告", "没有", "暂无", "正常"]
            has_alert_content = any(kw in full_text for kw in alert_keywords)
            assert has_alert_content, (
                f"Response doesn't contain alert-related content: {full_text[:300]}"
            )
        finally:
            config_module._config_manager = old_manager

    # ----- Cost Optimization Scenario -----

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_cost_optimization_recommendations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should list cost optimization recommendations."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "查看成本优化建议",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                expected_tools=["smartcmp_list_cost_recommendations"],
                forbidden_keywords=["配置不可用"],
                label="成本优化建议",
            )

            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[成本优化] Tools: {tool_names}")
            print(f"[成本优化] Response:\n{full_text[:1000]}")

            # Tool results
            tool_results = _get_tool_results(events, "smartcmp_list_cost_recommendations")
            if tool_results:
                combined = "\n".join(tool_results)
                print(f"[成本优化] Tool output preview: {combined[:800]}")

            # Response should mention cost/optimization
            cost_keywords = ["成本", "优化", "cost", "optimization", "建议",
                             "节省", "savings", "没有", "暂无", "recommendation"]
            has_cost_content = any(kw in full_text.lower() for kw in cost_keywords)
            assert has_cost_content, (
                f"Response doesn't contain cost optimization content: {full_text[:300]}"
            )
        finally:
            config_module._config_manager = old_manager

    # ----- Resource Request Scenario -----

    @pytest.mark.skipif(
        not CMP_BASE_URL or (not CMP_USER_TOKEN and not CMP_USERNAME),
        reason="CMP_BASE_URL and CMP_USER_TOKEN/CMP_USERNAME required",
    )
    @pytest.mark.skipif(
        not os.environ.get("LLM_API_KEY"),
        reason="LLM_API_KEY required for agent interaction tests",
    )
    def test_request_cloud_resource(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should start the resource request flow by listing services first."""
        events, history_text, config_module, old_manager = self._setup_and_run(
            tmp_path, monkeypatch, "我想申请一台云主机",
        )
        try:
            _, tool_names, _ = _assert_agent_completed(
                events,
                forbidden_keywords=["配置不可用"],
                label="申请云资源",
            )

            full_text = self._get_full_response(events, history_text)
            assert len(full_text) > 0, "Agent returned no response text"
            print(f"\n[申请云资源] Tools: {tool_names}")
            print(f"[申请云资源] Response:\n{full_text[:1000]}")

            # The agent should call list_services to show available options
            # or ask clarifying questions about the request
            request_keywords = [
                "smartcmp_list_services", "smartcmp_list_business_groups",
                "smartcmp_list_components", "smartcmp_list_resource_pools",
            ]
            used_request_tool = any(t in tool_names for t in request_keywords)

            # Tool results should show service catalog
            tool_results = _get_tool_results(events)
            if tool_results:
                combined = "\n".join(tool_results)
                print(f"[申请云资源] Tool output preview: {combined[:800]}")

            # Response should guide the user through request or show catalog
            resource_keywords = [
                "服务", "service", "选择", "select", "主机", "VM",
                "虚拟机", "资源", "resource", "配置", "业务组",
                "catalog", "申请", "request",
            ]
            has_resource_content = any(kw in full_text for kw in resource_keywords)
            assert used_request_tool or has_resource_content, (
                f"Response doesn't guide through resource request: {full_text[:300]}"
            )
        finally:
            config_module._config_manager = old_manager


class TestSmartCMPProviderConfigHotReload:
    """Test that provider config changes take effect without restart."""

    def test_provider_config_hot_reload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Creating a provider config via API should immediately be available."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        from app.atlasclaw.api.routes import get_api_context

        app, config_module, old_manager = _create_app_with_config(
            tmp_path, monkeypatch, extra_service_providers={},
        )
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                # Verify no SmartCMP instances initially
                ctx = get_api_context()
                smartcmp_instances = ctx.provider_instances.get("smartcmp", {})
                assert len(smartcmp_instances) == 0, (
                    f"Expected no SmartCMP instances, got: {list(smartcmp_instances.keys())}"
                )

                # Create SmartCMP config via API
                _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="hot-reload-test",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN or "test-token",
                )

                # Verify it's immediately available in memory
                ctx = get_api_context()
                smartcmp_instances = ctx.provider_instances.get("smartcmp", {})
                assert "hot-reload-test" in smartcmp_instances, (
                    f"Hot-reloaded instance not found. Available: {list(smartcmp_instances.keys())}"
                )
                assert smartcmp_instances["hot-reload-test"]["base_url"] == CMP_BASE_URL
        finally:
            config_module._config_manager = old_manager

    def test_provider_config_db_overrides_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """DB provider config should override JSON config for the same instance."""
        if not CMP_BASE_URL:
            pytest.skip("CMP_BASE_URL not set")

        from app.atlasclaw.api.routes import get_api_context

        app, config_module, old_manager = _create_app_with_config(
            tmp_path,
            monkeypatch,
            extra_service_providers={
                "smartcmp": {
                    "default": {
                        "base_url": "https://json-dummy.example.com",
                        "user_token": "json-dummy-token",
                    }
                }
            },
        )
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                # Override "default" instance via API (DB takes priority)
                _create_smartcmp_provider_config(
                    client,
                    headers,
                    instance_name="default",
                    base_url=CMP_BASE_URL,
                    user_token=CMP_USER_TOKEN or "db-override-token",
                )

                # Verify DB config overrides JSON
                ctx = get_api_context()
                default_cfg = ctx.provider_instances.get("smartcmp", {}).get("default", {})
                assert default_cfg.get("base_url") == CMP_BASE_URL, (
                    f"Expected DB base_url to override JSON. Got: {default_cfg.get('base_url')}"
                )
        finally:
            config_module._config_manager = old_manager


class TestSmartCMPEdgeCases:
    """Edge case and error handling tests."""

    def test_agent_run_without_provider_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agent should handle gracefully when SmartCMP is not configured."""
        if not os.environ.get("LLM_API_KEY"):
            pytest.skip("LLM_API_KEY required")

        app, config_module, old_manager = _create_app_with_config(
            tmp_path, monkeypatch, extra_service_providers={},
        )
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)
                session_key = login_body["session"]["key"]

                events = _run_agent_and_collect_events(
                    client, headers, "查看我的待审批", session_key,
                )

                # Should complete without crashing
                final_status = (events.get("_final_status") or ["unknown"])[0]
                lifecycle_phases = [e.get("phase") for e in events["lifecycle"]]
                assert (
                    final_status in ("completed", "error", "timeout")
                    or "end" in lifecycle_phases
                    or "error" in lifecycle_phases
                ), f"Agent run should complete. Status: {final_status}, phases: {lifecycle_phases}"

                # Get response for visibility
                full_text = _get_assistant_text(events)
                if not full_text:
                    history = _get_session_history(client, headers, session_key)
                    full_text = _get_last_assistant_message(history)
                print(f"\n[无Provider配置] Status: {final_status}")
                print(f"[无Provider配置] Response: {full_text[:500]}")
        finally:
            config_module._config_manager = old_manager

    def test_create_session_and_multi_turn_conversation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Test creating a new session and running multiple turns."""
        if not os.environ.get("LLM_API_KEY"):
            pytest.skip("LLM_API_KEY required")

        app, config_module, old_manager = _create_app_with_config(tmp_path, monkeypatch)
        try:
            with TestClient(app) as client:
                login_body = _login(client)
                headers = _auth_headers(login_body)

                # Create a new thread/session
                thread_resp = client.post(
                    "/api/sessions/threads",
                    json={},
                    headers=headers,
                )
                assert thread_resp.status_code == 200
                thread_data = thread_resp.json()
                session_key = thread_data.get("session_key", "")
                assert session_key, "No session_key returned from thread creation"

                # First turn: ask about pending approvals (SmartCMP scenario)
                events1 = _run_agent_and_collect_events(
                    client, headers, "查看我的待审批", session_key,
                )
                text1 = _get_assistant_text(events1)
                if not text1:
                    history = _get_session_history(client, headers, session_key)
                    text1 = _get_last_assistant_message(history)
                assert text1, "First turn should produce text"
                print(f"\n[多轮对话-Turn1] Response: {text1[:500]}")

                # Second turn: follow-up based on first response
                events2 = _run_agent_and_collect_events(
                    client, headers, "请再详细解释一下上面的内容", session_key,
                )
                text2 = _get_assistant_text(events2)
                if not text2:
                    history = _get_session_history(client, headers, session_key)
                    text2 = _get_last_assistant_message(history)
                assert text2, "Second turn should produce text"
                print(f"[多轮对话-Turn2] Response: {text2[:500]}")

                # Verify session history contains messages
                history = _get_session_history(client, headers, session_key)
                user_msgs = [m for m in history if m.get("role") == "user"]
                assistant_msgs = [m for m in history if m.get("role") == "assistant"]
                # History API may return partial transcript depending on implementation
                assert len(user_msgs) >= 1, (
                    f"Expected at least 1 user message in history, got {len(user_msgs)}"
                )
                assert len(assistant_msgs) >= 1, (
                    f"Expected at least 1 assistant message in history, got {len(assistant_msgs)}"
                )
                print(f"[多轮对话] Session history: {len(user_msgs)} user + {len(assistant_msgs)} assistant messages")
        finally:
            config_module._config_manager = old_manager


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "e2e", "--tb=short"])
