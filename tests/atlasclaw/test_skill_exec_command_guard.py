# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for the skill runtime command guard: inline code, paths, URL arguments."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.tools.skill_runtime_tools import (
    _validate_runtime_command_paths,
    skill_exec_tool,
)


def _python_exec() -> str:
    resolved = shutil.which("python") or shutil.which("python3")
    return Path(resolved).name if resolved else "python"


def _make_ctx(tmp_path: Path) -> tuple[SimpleNamespace, Path, Path]:
    skill_dir = tmp_path / "skills" / "report"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: report\ndescription: report\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "report",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)
    work_dir = workspace / "users" / "u1" / "work_dir"
    return ctx, skill_dir, work_dir


def _run(ctx: SimpleNamespace, command: str) -> dict:
    return asyncio.run(skill_exec_tool(ctx, command=command))


class TestInlineCodeRejection:
    def test_python_dash_c_is_rejected(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, "python -c \"import os;print(open('/etc/passwd').read())\"")
        assert result["is_error"] is True
        assert "inline code execution is not allowed" in result["content"][0]["text"]

    def test_shell_dash_c_is_rejected(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, "bash -c \"cat /etc/passwd\"")
        assert result["is_error"] is True
        assert "inline code execution is not allowed" in result["content"][0]["text"]

    def test_python_dash_m_is_rejected(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, "python -m http.server 8000")
        assert result["is_error"] is True
        assert "inline code execution is not allowed" in result["content"][0]["text"]

    def test_env_wrapper_is_not_a_bypass(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, "env python -c \"print('x')\"")
        assert result["is_error"] is True
        assert "inline code execution is not allowed" in result["content"][0]["text"]

    def test_flags_after_script_name_are_allowed(self, tmp_path: Path):
        ctx, _, work_dir = _make_ctx(tmp_path)
        work_dir.mkdir(parents=True, exist_ok=True)
        script = work_dir / "report.py"
        script.write_text(
            "import sys\nprint('flags:', sys.argv[1:])\n",
            encoding="utf-8",
        )
        result = _run(ctx, f"{_python_exec()} report.py --verbose -e fast")
        assert result["is_error"] is False, result["content"][0]["text"]
        assert "inline code" not in result["content"][0]["text"]


class TestPathGuard:
    def test_windows_drive_path_argument_is_rejected(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, f"{_python_exec()} readfile.py C:/Users/someone/.ssh/id_rsa")
        assert result["is_error"] is True
        assert "must stay inside work_dir" in result["content"][0]["text"]

    def test_windows_backslash_path_is_rejected_at_guard_level(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        try:
            _validate_runtime_command_paths(
                ctx,
                "python readfile.py",
                ["python", "readfile.py", "C:\\Users\\someone\\.ssh\\id_rsa"],
            )
        except ValueError as exc:
            assert "must stay inside work_dir" in str(exc)
        else:
            raise AssertionError("backslash windows path passed the guard")

    def test_posix_absolute_path_argument_is_rejected(self, tmp_path: Path):
        ctx, _, _ = _make_ctx(tmp_path)
        result = _run(ctx, f"{_python_exec()} readfile.py /etc/passwd")
        assert result["is_error"] is True
        assert "must stay inside work_dir" in result["content"][0]["text"]

    def test_url_arguments_are_not_misread_as_paths(self, tmp_path: Path):
        ctx, _, work_dir = _make_ctx(tmp_path)
        work_dir.mkdir(parents=True, exist_ok=True)
        script = work_dir / "fetch_report.py"
        script.write_text(
            "import sys\nprint('url:', sys.argv[1])\n",
            encoding="utf-8",
        )
        result = _run(
            ctx,
            f"{_python_exec()} fetch_report.py https://example.com/api/reports",
        )
        assert result["is_error"] is False, result["content"][0]["text"]
        text = result["content"][0]["text"]
        assert "must stay inside" not in text
        assert "inline code" not in text
