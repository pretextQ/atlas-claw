# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-14 regression tests: skill registry input robustness.

Covers: malformed args JSON returns a structured error instead of raising
out of execute() (F-0050), and markdown skills whose frontmatter has null
or non-string name/description are rejected explicitly instead of crashing
the loader (F-0051).
"""

from __future__ import annotations

import json

import pytest

from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


class TestExecuteArgumentParsing:
    @pytest.mark.asyncio
    async def test_malformed_args_json_returns_error_object(self, registry):
        async def handler(**kwargs) -> str:
            return "ok"

        registry.register(SkillMetadata(name="dummy", description="d"), handler)

        result = await registry.execute("dummy", "{bad json")
        payload = json.loads(result)
        assert "error" in payload
        assert "Invalid arguments JSON" in payload["error"]

    @pytest.mark.asyncio
    async def test_non_object_args_json_returns_error_object(self, registry):
        async def handler(**kwargs) -> str:
            return "ok"

        registry.register(SkillMetadata(name="dummy", description="d"), handler)

        result = await registry.execute("dummy", "[1, 2, 3]")
        payload = json.loads(result)
        assert "error" in payload
        assert "JSON object" in payload["error"]

    @pytest.mark.asyncio
    async def test_valid_args_still_execute(self, registry):
        async def handler(value: str = "") -> str:
            return f"got:{value}"

        registry.register(SkillMetadata(name="dummy", description="d"), handler)

        result = await registry.execute("dummy", json.dumps({"value": "x"}))
        assert result == "got:x"


class TestFrontmatterValidation:
    def _write_skill(self, tmp_path, frontmatter: str) -> "object":
        skill_dir = tmp_path / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(f"---\n{frontmatter}\n---\n\nBody\n", encoding="utf-8")
        return skill_file

    def test_null_description_is_rejected_without_crashing(self, registry, tmp_path):
        self._write_skill(
            tmp_path,
            "name: demo-skill\ndescription: null",
        )
        loaded = registry.load_from_directory(str(tmp_path / "skills"))
        assert loaded == 0
        assert "demo-skill" not in registry._md_skills

    def test_non_string_description_is_rejected(self, registry, tmp_path):
        self._write_skill(
            tmp_path,
            "name: demo-skill\ndescription:\n  - a\n  - b",
        )
        loaded = registry.load_from_directory(str(tmp_path / "skills"))
        assert loaded == 0

    def test_null_name_is_rejected(self, registry, tmp_path):
        self._write_skill(
            tmp_path,
            "name: null\ndescription: A demo skill",
        )
        loaded = registry.load_from_directory(str(tmp_path / "skills"))
        assert loaded == 0

    def test_valid_frontmatter_still_loads(self, registry, tmp_path):
        self._write_skill(
            tmp_path,
            "name: demo-skill\ndescription: A demo skill",
        )
        loaded = registry.load_from_directory(str(tmp_path / "skills"))
        assert loaded == 1
        assert "demo-skill" in registry._md_skills
