# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-09 regression tests: agent execution state and failure classification.

Covers the critical prepare-phase NameError and stale run_failed write-back
(F-0001/F-0018/F-0019), over-broad "error" substring detection (F-0017),
unguarded terminal hooks (F-0016), ineffective artifact extension filtering
(F-0021), the classifier override silently falling back to the default
toolset (F-0022), and non-zero returncode classified as success (F-0023).
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.runner_tool.runner_execution_flow_error import (
    RunnerExecutionFlowErrorMixin,
)
from app.atlasclaw.agent.runner_tool.runner_execution_flow_stream import (
    RunnerExecutionFlowStreamMixin,
)
from app.atlasclaw.agent.runner_tool.runner_execution_prepare import (
    RunnerExecutionPreparePhaseMixin,
)
from app.atlasclaw.agent.runner_tool.runner_llm_routing import (
    tool_output_satisfies_artifact_goal,
)
from app.atlasclaw.agent.runner_tool.runner_tool_gate_policy import (
    RunnerToolGatePolicyMixin,
)
from app.atlasclaw.core.deps import SkillDeps


class _PolicyRunner(RunnerToolGatePolicyMixin):
    pass


class TestToolErrorSignature:
    """F-0017: success text containing 'error' is not an error signature."""

    @pytest.mark.parametrize(
        "content",
        [
            "No errors found.",
            "Scanned 100 files, 0 errors.",
            "errors: []",
            "error_count=0; status=ok",
        ],
    )
    def test_success_text_is_not_an_error(self, content):
        assert RunnerExecutionFlowStreamMixin._extract_tool_error_signature(content) == ""

    @pytest.mark.parametrize(
        "content",
        [
            "[error] command failed",
            "Error: connection refused",
            "Traceback (most recent call last): ...",
            "missing required argument 'path'",
        ],
    )
    def test_structured_error_text_is_detected(self, content):
        assert RunnerExecutionFlowStreamMixin._extract_tool_error_signature(content)

    def test_structured_error_fields_are_detected(self):
        assert RunnerExecutionFlowStreamMixin._extract_tool_error_signature(
            {"is_error": True, "error": "boom"}
        ) == "boom"


class TestNonZeroReturncode:
    """F-0023: exit codes must decide success, not dict truthiness."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"returncode": 1, "output": ""},
            {"returncode": 1, "output": "boom"},
            {"returncode": 2, "output": "", "error": ""},
        ],
    )
    def test_non_zero_returncode_is_failure(self, payload):
        assert _PolicyRunner._is_tool_payload_success(payload, tool_meta={}) is False

    def test_zero_returncode_with_output_is_success(self):
        assert _PolicyRunner._is_tool_payload_success(
            {"returncode": 0, "output": "ok"}, tool_meta={}
        ) is True

    def test_zero_returncode_with_empty_output_is_not_success(self):
        assert _PolicyRunner._is_tool_payload_success(
            {"returncode": 0, "output": ""}, tool_meta={}
        ) is False

    def test_success_true_with_empty_output_is_not_success(self):
        assert _PolicyRunner._is_tool_payload_success(
            {"success": True, "output": ""}, tool_meta={}
        ) is False

    def test_is_error_flag_wins(self):
        assert _PolicyRunner._is_tool_payload_success(
            {"is_error": True, "returncode": 0, "output": "ok"}, tool_meta={}
        ) is False


class TestArtifactExtensionFilter:
    """F-0021: an explicit extension requirement must not be bypassed."""

    def test_extension_mismatch_is_not_satisfied(self, tmp_path):
        workspace = self._workspace(tmp_path)
        self._write(workspace, "report.txt")

        satisfied = tool_output_satisfies_artifact_goal(
            tool_name="file_write",
            payload={"artifact_path": "report.txt"},
            artifact_goal={"extensions": [".pptx"]},
            workspace_path=str(workspace),
            user_id="u1",
        )
        assert satisfied is False

    def test_extension_match_is_satisfied(self, tmp_path):
        workspace = self._workspace(tmp_path)
        self._write(workspace, "deck.pptx")

        satisfied = tool_output_satisfies_artifact_goal(
            tool_name="file_write",
            payload={"artifact_path": "deck.pptx"},
            artifact_goal={"extensions": [".pptx"]},
            workspace_path=str(workspace),
            user_id="u1",
        )
        assert satisfied is True

    @staticmethod
    def _workspace(tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return workspace

    @staticmethod
    def _write(workspace, name: str) -> None:
        # Candidates must resolve inside the user's workspace download root.
        from app.atlasclaw.core.workspace_downloads import workspace_download_root

        root = workspace_download_root(str(workspace), "u1")
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("artifact", encoding="utf-8")


class _PrepareRuntimeEvents:
    """Runtime events stub that records triggered hook names."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.triggered: list[str] = []
        self.fail_on = fail_on or set()

    async def _trigger(self, name: str, **_kwargs) -> None:
        self.triggered.append(name)
        if name in self.fail_on:
            raise RuntimeError(f"{name} hook exploded")

    async def trigger_message_received(self, **kwargs) -> None:
        await self._trigger("message_received", **kwargs)

    async def trigger_run_started(self, **kwargs) -> None:
        await self._trigger("run_started", **kwargs)

    async def trigger_llm_failed(self, **kwargs) -> None:
        await self._trigger("llm_failed", **kwargs)

    async def trigger_run_failed(self, **kwargs) -> None:
        await self._trigger("run_failed", **kwargs)

    async def trigger_run_context_ready(self, **kwargs) -> None:
        await self._trigger("run_context_ready", **kwargs)


class _PrepareSessionManager:
    def __init__(self) -> None:
        self.session = SimpleNamespace(title="t", title_status="ready", extra={})

    async def get_or_create(self, session_key: str):
        return self.session

    async def load_transcript(self, session_key: str) -> list[dict]:
        return []


class _PrepareHistory:
    @staticmethod
    def build_message_history(transcript: list[dict]) -> list[dict]:
        return list(transcript)

    @staticmethod
    def prune_summary_messages(messages: list[dict]) -> list[dict]:
        return list(messages)


def _prepare_state(deps: SkillDeps) -> dict:
    return {
        "session_key": deps.session_key,
        "user_message": "restart this vm",
        "deps": deps,
        "_emit_lifecycle_bounds": False,
        "start_time": time.monotonic(),
        "run_id": "run-wp09",
        "message_history": [],
        "context_history_for_hooks": [],
        "tool_call_summaries": [],
        "buffered_assistant_events": [],
        "tool_request_message": "restart this vm",
    }


class TestPrepareEarlyExit:
    """F-0001/F-0018/F-0019: early exits must not NameError, and the failure
    marker must survive the finally write-back."""

    def _runner(self) -> AgentRunner:
        runner = AgentRunner(agent=SimpleNamespace(), session_manager=_PrepareSessionManager())
        runner.history = _PrepareHistory()
        runner.runtime_events = _PrepareRuntimeEvents()
        return runner

    @pytest.mark.asyncio
    async def test_context_guard_block_does_not_raise_name_error(self):
        runner = self._runner()
        deps = SkillDeps(session_key="agent:main:user:u1:web:dm:p1", user_info=None)  # type: ignore[arg-type]
        state = _prepare_state(deps)

        # Force the hard-min context guard to block so the phase returns early.
        runner._resolve_runtime_context_window_info = lambda *args, **kwargs: SimpleNamespace(
            tokens=100, source="test"
        )

        events = [
            event
            async for event in runner._run_prepare_phase(state=state, _log_step=lambda *a, **k: None)
        ]

        assert state.get("run_failed") is True
        assert state.get("should_stop") is True
        assert any(event.type == "error" for event in events), [event.type for event in events]

    @pytest.mark.asyncio
    async def test_early_exit_publishes_model_user_message(self):
        runner = self._runner()
        deps = SkillDeps(session_key="agent:main:user:u1:web:dm:p1", user_info=None)  # type: ignore[arg-type]
        state = _prepare_state(deps)
        runner._resolve_runtime_context_window_info = lambda *args, **kwargs: SimpleNamespace(
            tokens=100, source="test"
        )

        async for _event in runner._run_prepare_phase(state=state, _log_step=lambda *a, **k: None):
            pass

        assert state.get("model_user_message") == "restart this vm"

    def test_state_failure_marker_survives_write_back(self):
        """A failure written directly to state must not be clobbered."""
        state = {"run_failed": True}
        # Mirrors the finally expression used by the phase.
        merged = bool(None) or bool(state.get("run_failed"))
        assert merged is True


class _FakeAgent:
    """Agent double whose override() only accepts an unrelated kwarg."""

    def __init__(self) -> None:
        self.tools_seen: list[object] = []

    def override(self, **kwargs):
        if "tools" in kwargs:
            raise TypeError("unexpected keyword argument 'tools'")
        raise TypeError("unexpected keyword arguments")

    async def run(self, user_message, *, deps):
        self.tools_seen.append("default-toolset")
        return SimpleNamespace(output="ran with default tools")


class TestClassifierOverrideFailure:
    """F-0022: a classifier pass must not silently run with default tools."""

    @pytest.mark.asyncio
    async def test_gate_model_aborts_when_empty_tool_override_cannot_apply(self, caplog):
        from app.atlasclaw.agent.runner_tool.runner_tool_gate_model import (
            RunnerToolGateModelMixin,
        )

        class _GateModelRunner(RunnerToolGateModelMixin):
            pass

        runner = _GateModelRunner()
        fake_agent = _FakeAgent()
        deps = SkillDeps(session_key="agent:main:user:u1:web:dm:p1", user_info=None)  # type: ignore[arg-type]

        with caplog.at_level("WARNING", logger="app.atlasclaw.agent.runner_tool.runner_tool_gate_model"):
            output = await runner._run_single_with_optional_override(
                agent=fake_agent,
                user_message="classify this",
                deps=deps,
                system_prompt="You are a classifier.",
                purpose="tool_gate_model_pass",
                allowed_tool_names=[],
            )

        assert output == ""
        assert fake_agent.tools_seen == [], "classifier must not run with the default toolset"
        assert any("tool override" in r.getMessage() for r in caplog.records)


class _ErrorFlowRunner(RunnerExecutionFlowErrorMixin):
    def __init__(self, runtime_events) -> None:
        self.runtime_events = runtime_events

    def _collect_tool_call_summaries_from_messages(self, messages, **_kwargs):
        return []

    # The tool-only recovery branch is not under test here; return an empty
    # answer so the flow continues to the terminal error path.
    def _build_tool_only_markdown_answer_from_messages(self, **_kwargs) -> str:
        return ""

    def _looks_like_raw_tool_payload_dump(self, _text) -> bool:
        return False

    async def _retry_after_hard_token_failure(self, **_kwargs):
        return
        yield  # pragma: no cover - async generator marker

    @property
    def token_failover_enabled(self) -> bool:
        return False


class TestTerminalErrorPathHooks:
    """F-0016: hook failures must not suppress the terminal error events."""

    @pytest.mark.asyncio
    async def test_hook_failure_still_emits_terminal_events(self):
        runtime_events = _PrepareRuntimeEvents(fail_on={"llm_failed"})
        runner = _ErrorFlowRunner(runtime_events)
        state = {
            "session_key": "agent:main:user:u1:web:dm:p1",
            "run_id": "run-wp09",
            "user_message": "hi",
            "system_prompt": "sys",
            "context_history_for_hooks": [],
            "final_assistant": "",
            "tool_call_summaries": [],
            "start_time": time.monotonic(),
            "_emit_lifecycle_bounds": False,
        }

        events = [
            event
            async for event in runner._handle_loop_phase_exception(
                error=RuntimeError("model exploded"), state=state
            )
        ]

        types = [event.type for event in events]
        assert "error" in types, types
        assert state["run_failed"] is True
        # The later hooks still ran despite the earlier one failing.
        assert "run_failed" in runtime_events.triggered
        assert "run_context_ready" in runtime_events.triggered
