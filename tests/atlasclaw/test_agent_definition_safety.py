# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-17 regression tests: agent definition path safety and defaults.

Covers: agent_id must be a plain directory name — traversal and absolute
paths are rejected before any path is built (F-0010) — and the default
capabilities list is copied per agent instead of shared by reference
(F-0011).
"""

from __future__ import annotations

import pytest

from app.atlasclaw.agent.agent_definition import AgentLoader


@pytest.fixture
def loader(tmp_path) -> AgentLoader:
    (tmp_path / "agents").mkdir(parents=True, exist_ok=True)
    return AgentLoader(workspace_path=tmp_path)


class TestAgentIdValidation:
    @pytest.mark.parametrize(
        "agent_id",
        [
            "../outside",
            "..",
            ".",
            "a/b",
            "a\\b",
            "C:\\windows",
            "/etc/passwd",
            "",
        ],
    )
    def test_unsafe_ids_are_rejected(self, loader, agent_id):
        with pytest.raises(ValueError):
            loader.load_agent(agent_id)

    def test_traversal_target_file_is_never_read(self, loader, tmp_path):
        """A ../agents sibling directory must not be loaded."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "SOUL.md").write_text(
            "---\nsystem_prompt: LEAKED\n---\n", encoding="utf-8"
        )

        with pytest.raises(ValueError):
            loader.load_agent("../outside")

    def test_plain_name_is_accepted(self, loader):
        config = loader.load_agent("main")
        assert config.agent_id == "main"


class TestDefaultCapabilitiesIsolation:
    def test_default_capabilities_are_copied_per_agent(self, loader):
        first = loader.load_agent("agent-a")
        second = loader.load_agent("agent-b")

        first.capabilities.append("only-for-a")

        assert "only-for-a" not in second.capabilities
        assert "only-for-a" not in loader.DEFAULT_CONFIG.capabilities

    def test_defaults_are_still_populated(self, loader):
        config = loader.load_agent("agent-a")
        assert config.capabilities == list(loader.DEFAULT_CONFIG.capabilities)
        assert config.system_prompt == loader.DEFAULT_CONFIG.system_prompt
