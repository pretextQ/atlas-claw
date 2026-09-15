# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.atlasclaw.api.agent_info import router as agent_info_router
import app.atlasclaw.core.config as config_module


@pytest.fixture(autouse=True)
def reset_config_manager():
    config_module._config_manager = None
    yield
    config_module._config_manager = None


def test_agent_info_uses_soul_name_without_yaml_quotes(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        '{\n  "workspace": {\n    "path": ".atlasclaw"\n  }\n}\n',
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".atlasclaw" / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "SOUL.md").write_text(
        '---\nagent_id: "main"\nname: "Enterprise Assistant"\ndescription: "Helpful assistant"\n---\n',
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(agent_info_router)
    client = TestClient(app)

    response = client.get("/api/agent/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Enterprise Assistant"
    assert payload["welcome_message"].startswith("Hello! I'm Enterprise Assistant.")


def test_agent_info_reads_workspace_from_config(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    workspace_path = tmp_path / "custom-workspace"
    agent_dir = workspace_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "SOUL.md").write_text(
        '---\nagent_id: "main"\nname: "Configured Workspace Agent"\ndescription: "From configured workspace"\n---\n',
        encoding="utf-8",
    )

    config_path.write_text(
        '{\n  "workspace": {\n    "path": "custom-workspace"\n  }\n}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    monkeypatch.chdir(tmp_path)

    app = FastAPI()
    app.include_router(agent_info_router)
    client = TestClient(app)

    response = client.get("/api/agent/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Configured Workspace Agent"
    assert payload["welcome_message"].startswith("Hello! I'm Configured Workspace Agent.")


def test_agent_info_uses_soul_system_prompt_first_paragraph_as_description(tmp_path, monkeypatch):
    config_path = tmp_path / "atlasclaw.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATLASCLAW_CONFIG", str(config_path))
    config_path.write_text(
        '{\n  "workspace": {\n    "path": ".atlasclaw"\n  }\n}\n',
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".atlasclaw" / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "SOUL.md").write_text(
        '---\nagent_id: "main"\nname: "AtlasClaw Enterprise AI Assistant"\nversion: "1.0"\n---\n\n'
        "## System Prompt\n\n"
        "AtlasClaw is an enterprise-grade AI assistant positioned as an intelligent collaboration partner for employees.\n\n"
        "- Business Understanding: Comprehension of enterprise business processes and terminology\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(agent_info_router)
    client = TestClient(app)

    response = client.get("/api/agent/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["description"] == (
        "AtlasClaw is an enterprise-grade AI assistant positioned as an intelligent "
        "collaboration partner for employees."
    )
    assert payload["welcome_message"] == (
        "Hello! I'm AtlasClaw Enterprise AI Assistant.\n\n"
        "AtlasClaw is an enterprise-grade AI assistant positioned as an intelligent "
        "collaboration partner for employees."
    )
