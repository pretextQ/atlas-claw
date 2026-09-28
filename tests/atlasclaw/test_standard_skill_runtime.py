# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

from app.atlasclaw.agent.runner_prompt_context import collect_tools_snapshot
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    inject_standard_skill_runtime_tools,
)
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.skills.registry import SkillRegistry
from app.atlasclaw.tools import skill_runtime_tools
from app.atlasclaw.tools.registration import register_builtin_tools
from app.atlasclaw.tools.skill_runtime_tools import (
    _runtime_dirs,
    _runtime_env,
    skill_exec_tool,
    skill_process_tool,
    skill_write_tool,
)


def _python_exec() -> str:
    """Return a bare interpreter name usable by the runtime on this platform."""
    resolved = shutil.which("python") or shutil.which("python3")
    return Path(resolved).name if resolved else "python"


def test_standard_skill_runtime_tools_are_registered_but_hidden() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)

    assert registry.get("skill_exec") is not None
    assert registry.get("skill_write") is not None
    assert "skill_exec" not in {item["name"] for item in registry.tools_snapshot()}
    assert "skill_exec" not in {item["name"] for item in registry.snapshot()}
    assert "skill_exec" in {
        item["name"] for item in registry.internal_runtime_tools_snapshot()
    }


def test_collect_tools_snapshot_hides_standard_runtime_tools_without_flag() -> None:
    registry = SkillRegistry()
    register_builtin_tools(registry)
    agent = SimpleNamespace()
    registry.register_to_agent(agent)

    hidden = {item["name"] for item in collect_tools_snapshot(agent=agent)}
    internal_tools = registry.internal_runtime_tools_snapshot()
    visible = {
        item["name"]
        for item in collect_tools_snapshot(
            agent=agent,
            deps=SimpleNamespace(
                extra={
                    "standard_skill_runtime_tools_visible": True,
                    "tools_snapshot": internal_tools,
                    "tools_snapshot_authoritative": True,
                }
            ),
        )
    }

    assert "skill_exec" not in hidden
    assert "skill_exec" in visible


def test_standard_runtime_injects_only_for_docs_only_target_skill() -> None:
    runtime_tools = [
        {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
        {"name": "skill_write", "description": "write", "source": "internal_runtime"},
    ]
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": runtime_tools,
            "md_skills_snapshot": [
                {
                    "name": "xlsx",
                    "qualified_name": "xlsx",
                    "metadata": {},
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[],
        deps=deps,
        target_md_skill={
            "qualified_name": "xlsx",
            "file_path": "/tmp/skills/xlsx/SKILL.md",
        },
    )

    assert trace["enabled"] is True
    assert {item["name"] for item in available} == {"skill_exec", "skill_write"}
    assert deps.extra["standard_skill_runtime_enabled"] is True
    assert target and target["standard_runtime_enabled"] is True


def test_standard_runtime_does_not_inject_when_skill_declares_tool() -> None:
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": [
                {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
            ],
            "md_skills_snapshot": [
                {
                    "name": "pptx",
                    "qualified_name": "pptx",
                    "metadata": {
                        "tool_create_name": "pptx_create_deck",
                        "tool_create_entrypoint": "scripts/handler.py:create_deck_handler",
                    },
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[{"name": "pptx_create_deck"}],
        deps=deps,
        target_md_skill={
            "qualified_name": "pptx",
            "file_path": "/tmp/skills/pptx/SKILL.md",
        },
    )

    assert trace["enabled"] is False
    assert trace["reason"] == "target_skill_has_executable_tool"
    assert available == [{"name": "pptx_create_deck"}]
    assert target and "standard_runtime_enabled" not in target


def test_standard_runtime_does_not_inject_for_provider_bound_docs_only_skill() -> None:
    deps = SkillDeps(
        extra={
            "internal_runtime_tools_snapshot": [
                {"name": "skill_exec", "description": "exec", "source": "internal_runtime"},
            ],
            "md_skills_snapshot": [
                {
                    "name": "export",
                    "qualified_name": "acme:export",
                    "provider": "acme",
                    "metadata": {"provider_type": "acme"},
                }
            ],
        }
    )

    available, trace, target = inject_standard_skill_runtime_tools(
        available_tools=[],
        deps=deps,
        target_md_skill={
            "provider": "acme",
            "qualified_name": "acme:export",
            "file_path": "/tmp/skills/acme/export/SKILL.md",
        },
    )

    assert trace["enabled"] is False
    assert trace["reason"] == "target_skill_provider_bound"
    assert available == []
    assert target and "standard_runtime_enabled" not in target


def test_skill_exec_uses_work_dir_scoped_home_and_tmp(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    command_script = skill_dir / "dump_env.py"
    command_script.write_text(
        "import json, os, pathlib\n"
        "pathlib.Path('env.json').write_text(json.dumps({"
        "'work': os.environ['ATLASCLAW_WORK_DIR'], "
        "'skill': os.environ['ATLASCLAW_SKILL_DIR'], "
        "'home': os.environ['HOME'], "
        "'tmp': os.environ['TMPDIR'], "
        "'config': os.environ['XDG_CONFIG_HOME']}))\n",
        encoding="utf-8",
    )
    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=f'{_python_exec()} "{command_script.as_posix()}"'
        )
    )

    assert result["is_error"] is False
    env_file = workspace / "users" / "u1" / "work_dir" / "env.json"
    payload = json.loads(env_file.read_text(encoding="utf-8"))
    assert payload["work"] == str(workspace / "users" / "u1" / "work_dir")
    assert payload["skill"] == str(skill_dir)
    assert payload["home"].startswith(payload["work"])
    assert payload["tmp"].startswith(payload["work"])
    assert payload["config"].startswith(payload["work"])


def test_standard_runtime_env_does_not_inherit_server_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ATLASCLAW_SECRET_FOR_TEST", "must-not-leak")
    monkeypatch.setenv("PATH", "/bin:/usr/bin")

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    env = _runtime_env(ctx)

    assert env["PATH"] == "/bin:/usr/bin"
    assert env["ATLASCLAW_WORK_DIR"] == str(workspace / "users" / "u1" / "work_dir")
    assert "ATLASCLAW_SECRET_FOR_TEST" not in env


def test_skill_exec_returns_explicit_download_paths_for_generated_files(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    generator = skill_dir / "gen_report.py"
    generator.write_text(
        "from pathlib import Path\n"
        "Path('report.pdf').write_bytes(b'%PDF-1.4\\n')\n"
        "Path('tmp.log').write_text('debug')\n",
        encoding="utf-8",
    )
    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=f'{_python_exec()} "{generator.as_posix()}"',
            download_paths=["report.pdf"],
        )
    )

    assert result["is_error"] is False
    assert result["details"]["download_path"] == ["report.pdf"]


def test_skill_exec_does_not_infer_download_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    generator = skill_dir / "gen_report.py"
    generator.write_text(
        "from pathlib import Path\n"
        "Path('report.pdf').write_bytes(b'%PDF-1.4\\n')\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=f'{_python_exec()} "{generator.as_posix()}"',
        )
    )

    assert result["is_error"] is False
    assert "download_path" not in result["details"]
    assert "No download_paths were provided" in result["content"][0]["text"]


def test_skill_exec_rejects_hidden_runtime_download_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    hidden_file = (
        workspace / "users" / "u1" / "work_dir"
        / ".atlasclaw" / "skills" / "skill-pdf" / "cache" / "debug.pdf"
    )
    hidden_file.parent.mkdir(parents=True, exist_ok=True)
    hidden_file.write_bytes(b"debug")

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=f"{_python_exec()} --version",
            download_paths=[".atlasclaw/skills/skill-pdf/cache/debug.pdf"],
        )
    )

    assert result["is_error"] is True
    assert "download_paths must reference visible files" in result["content"][0]["text"]


def test_skill_exec_rejects_missing_hidden_download_path(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=f"{_python_exec()} --version",
            download_paths=[".atlasclaw/skills/skill-pdf/cache/missing.pdf"],
        )
    )

    assert result["is_error"] is True
    assert "download_paths must reference visible files" in result["content"][0]["text"]


def test_skill_exec_rejects_home_relative_command_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                "Path('~/bad.pdf').expanduser().write_text('bad')\""
            ),
        )
    )

    assert result["is_error"] is True
    assert "~" in result["content"][0]["text"]


def test_skill_exec_rejects_home_relative_user_requested_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    deps.user_message = "Generate a PDF at ~/bad.pdf"
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command="python -c \"print('would create file')\"",
        )
    )

    assert result["is_error"] is True
    assert "home-relative output paths are not allowed" in result["content"][0]["text"]


def test_skill_exec_rejects_absolute_command_paths_outside_work_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: pdf\ndescription: pdf\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.pdf"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "pdf",
                "file_path": str(skill_file),
            },
        },
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_exec_tool(
            ctx,
            command=(
                "python -c \"from pathlib import Path; "
                f"Path({str(outside)!r}).write_text('bad')\""
            ),
        )
    )

    assert result["is_error"] is True
    assert "inline code execution is not allowed" in result["content"][0]["text"]
    assert not outside.exists()


def test_standard_runtime_tools_reject_direct_invocation_without_selected_skill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    deps = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_manager=SimpleNamespace(workspace_path=workspace),
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        skill_write_tool(ctx, file_path="leak.txt", content="should not write")
    )

    assert result["is_error"] is True
    assert not (workspace / "users" / "u1" / "work_dir" / "leak.txt").exists()


def test_standard_runtime_processes_are_scoped_to_user_session_and_skill(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    deps_one = SkillDeps(
        user_info=UserInfo(user_id="u1", display_name="User One"),
        session_key="s1",
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    deps_two = SkillDeps(
        user_info=UserInfo(user_id="u2", display_name="User Two"),
        session_key="s1",
        session_manager=SimpleNamespace(workspace_path=workspace),
        extra={
            "standard_skill_runtime_enabled": True,
            "target_md_skill": {
                "qualified_name": "xlsx",
                "file_path": str(skill_file),
            },
        },
    )
    ctx_one = SimpleNamespace(deps=deps_one)
    ctx_two = SimpleNamespace(deps=deps_two)

    server = skill_dir / "serve.py"
    server.write_text(
        "import time\nprint('ready', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )

    async def run_case() -> None:
        start = await skill_process_tool(
            ctx_one,
            action="start",
            command=f'{_python_exec()} -u "{server.as_posix()}"',
        )
        process_id = start["details"]["process_id"]
        blocked = await skill_process_tool(ctx_two, action="poll", process_id=process_id)
        cleanup = await skill_process_tool(ctx_one, action="kill", process_id=process_id)

        assert start["is_error"] is False
        assert blocked["is_error"] is True
        assert cleanup["is_error"] is False

    asyncio.run(run_case())


def test_standard_runtime_skill_directories_do_not_collapse_similar_skill_ids(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")

    def ctx_for(qualified_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            deps=SkillDeps(
                user_info=UserInfo(user_id="u1", display_name="User One"),
                session_manager=SimpleNamespace(workspace_path=workspace),
                extra={
                    "standard_skill_runtime_enabled": True,
                    "target_md_skill": {
                        "qualified_name": qualified_name,
                        "file_path": str(skill_file),
                    },
                },
            )
        )

    first_root = _runtime_dirs(ctx_for("a:b"))["root"]
    second_root = _runtime_dirs(ctx_for("a_b"))["root"]

    assert first_root != second_root


def _process_ctx(workspace: Path, skill_file: Path) -> SimpleNamespace:
    """Build a minimal ctx that may own long-lived skill processes."""
    return SimpleNamespace(
        deps=SkillDeps(
            user_info=UserInfo(user_id="u1", display_name="User One"),
            session_key="s1",
            session_manager=SimpleNamespace(workspace_path=workspace),
            extra={
                "standard_skill_runtime_enabled": True,
                "target_md_skill": {
                    "qualified_name": "xlsx",
                    "file_path": str(skill_file),
                },
            },
        )
    )


def test_standard_runtime_process_poll_reports_exit_and_reaps(tmp_path: Path) -> None:
    """A finished process must report its exit and not linger in the registry."""
    workspace = tmp_path / "workspace"
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")

    script = skill_dir / "quick.py"
    script.write_text("print('finished', flush=True)\n", encoding="utf-8")
    ctx = _process_ctx(workspace, skill_file)

    async def run_case() -> None:
        start = await skill_process_tool(
            ctx,
            action="start",
            command=f'{_python_exec()} -u "{script.as_posix()}"',
        )
        process_id = start["details"]["process_id"]
        # Wait for the child to exit.
        for _ in range(100):
            await asyncio.sleep(0.05)
            managed = skill_runtime_tools._PROCESSES.get(process_id)
            if managed is not None and managed.proc.returncode is not None:
                break

        final_poll = await skill_process_tool(ctx, action="poll", process_id=process_id)

        assert final_poll["is_error"] is False
        assert final_poll["details"]["status"] == "exited"
        assert final_poll["details"]["exit_code"] == 0
        # Output is delivered incrementally, so the line may have already been
        # returned by the start call; across both reads it must be visible.
        observed = start["content"][0]["text"] + final_poll["content"][0]["text"]
        assert "finished" in observed

        # The finished entry is reaped once its retention window passes.
        managed = skill_runtime_tools._PROCESSES[process_id]
        managed.exited_at = time.time() - skill_runtime_tools.EXITED_PROCESS_RETENTION_SECONDS - 1
        assert skill_runtime_tools._reap_exited_processes() >= 1
        assert process_id not in skill_runtime_tools._PROCESSES

    asyncio.run(run_case())


def test_standard_runtime_process_start_enforces_capacity(tmp_path: Path) -> None:
    """The tracked-process registry must refuse unbounded growth."""
    workspace = tmp_path / "workspace"
    skill_dir = tmp_path / "skills" / "xlsx"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: xlsx\ndescription: xlsx\n---\n", encoding="utf-8")
    ctx = _process_ctx(workspace, skill_file)

    class _FakeProcess:
        returncode = None
        stdout = None
        stdin = None

    saved = dict(skill_runtime_tools._PROCESSES)
    skill_runtime_tools._PROCESSES.clear()
    try:
        for index in range(skill_runtime_tools.MAX_TRACKED_PROCESSES):
            skill_runtime_tools._PROCESSES[f"fake-{index}"] = skill_runtime_tools._ManagedProcess(
                process_id=f"fake-{index}",
                owner_key="someone",
                proc=_FakeProcess(),
                command="sleep",
            )

        async def run_case() -> dict:
            return await skill_process_tool(ctx, action="start", command=_python_exec())

        result = asyncio.run(run_case())

        assert result["is_error"] is True
        assert "too many tracked processes" in result["content"][0]["text"]
    finally:
        skill_runtime_tools._PROCESSES.clear()
        skill_runtime_tools._PROCESSES.update(saved)


def test_standard_runtime_read_incremental_buffer_is_bounded() -> None:
    """Output buffers keep only a tail so a chatty process cannot grow memory."""
    managed = skill_runtime_tools._ManagedProcess(
        process_id="p1",
        owner_key="owner",
        proc=SimpleNamespace(returncode=None),
        command="chatty",
    )
    managed.MAX_BUFFER_CHARS = 32

    class _Stdout:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = list(chunks)

        async def read(self, size: int) -> bytes:
            del size
            return self._chunks.pop(0) if self._chunks else b""

    async def run_case() -> str:
        managed.proc = SimpleNamespace(
            stdout=_Stdout([b"a" * 40, b"b" * 40]),
            returncode=None,
        )
        return await managed.read_incremental()

    output = asyncio.run(run_case())

    # Only the newest MAX_BUFFER_CHARS characters are retained; the oldest
    # output is dropped instead of growing the buffer without bound.
    assert len(managed._buffer) <= managed.MAX_BUFFER_CHARS
    assert output == "b" * managed.MAX_BUFFER_CHARS
    assert managed._read_offset <= len(managed._buffer)


def test_standard_runtime_shutdown_kills_tracked_processes() -> None:
    """Application shutdown must terminate tracked skill processes."""
    killed: list[str] = []

    class _FakeProcess:
        returncode = None

        def kill(self) -> None:
            killed.append("killed")

        async def wait(self) -> int:
            self.returncode = -9
            return -9

    saved = dict(skill_runtime_tools._PROCESSES)
    skill_runtime_tools._PROCESSES.clear()
    try:
        for index in range(3):
            skill_runtime_tools._PROCESSES[f"p{index}"] = skill_runtime_tools._ManagedProcess(
                process_id=f"p{index}",
                owner_key="owner",
                proc=_FakeProcess(),
                command="sleep",
            )

        asyncio.run(skill_runtime_tools.shutdown_skill_processes())

        assert killed == ["killed"] * 3
        assert skill_runtime_tools._PROCESSES == {}
    finally:
        skill_runtime_tools._PROCESSES.clear()
        skill_runtime_tools._PROCESSES.update(saved)
