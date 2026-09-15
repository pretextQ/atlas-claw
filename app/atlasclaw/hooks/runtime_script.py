# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.atlasclaw.hooks.runtime_models import (
    HookEventEnvelope,
    HookEventType,
    HookScriptAction,
    HookScriptActionBatch,
    HookScriptActionType,
)


class HookScriptExecutionError(RuntimeError):
    """Raised when a configured script hook cannot be executed successfully."""


@dataclass
class HookScriptHandlerDefinition:
    """Runtime-ready script hook handler definition."""

    module_name: str
    event_types: set[HookEventType]
    command: list[str]
    timeout_seconds: int = 10
    enabled: bool = True
    cwd: Optional[str] = None
    priority: int = 100


@dataclass
class HookScriptExecutionResult:
    """Raw execution result for a script hook command."""

    stdout: str
    stderr: str
    returncode: int


class HookScriptRunner:
    """Execute local command hook handlers and parse structured stdout actions."""

    async def run(
        self,
        definition: HookScriptHandlerDefinition,
        event: HookEventEnvelope,
    ) -> HookScriptActionBatch:
        result = await self._execute(definition, event)
        return self._parse_stdout(result.stdout)

    async def _execute(
        self,
        definition: HookScriptHandlerDefinition,
        event: HookEventEnvelope,
    ) -> HookScriptExecutionResult:
        payload = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
        env = os.environ.copy()
        env.update(
            {
                "ATLASCLAW_HOOK_EVENT": event.event_type.value,
                "ATLASCLAW_USER_ID": event.user_id,
                "ATLASCLAW_SESSION_KEY": event.session_key,
                "ATLASCLAW_RUN_ID": event.run_id,
                "ATLASCLAW_MODULE": definition.module_name,
            }
        )
        cwd = str(Path(definition.cwd).resolve()) if definition.cwd else None
        try:
            process = await asyncio.create_subprocess_exec(
                *definition.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:  # pragma: no cover - subprocess spawn failures are platform-specific
            raise HookScriptExecutionError(
                f"Failed to start hook script for module '{definition.module_name}': {exc}"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=definition.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise HookScriptExecutionError(
                f"Hook script timed out for module '{definition.module_name}' after "
                f"{definition.timeout_seconds}s"
            ) from exc

        decoded_stdout = stdout.decode("utf-8", errors="replace").strip()
        decoded_stderr = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise HookScriptExecutionError(
                f"Hook script exited with code {process.returncode} for module "
                f"'{definition.module_name}': {decoded_stderr or decoded_stdout}"
            )
        return HookScriptExecutionResult(
            stdout=decoded_stdout,
            stderr=decoded_stderr,
            returncode=process.returncode,
        )

    def _parse_stdout(self, stdout: str) -> HookScriptActionBatch:
        if not stdout:
            return HookScriptActionBatch()
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HookScriptExecutionError(f"Hook script stdout is not valid JSON: {exc}") from exc
        actions_payload = payload.get("actions", [])
        if not isinstance(actions_payload, list):
            raise HookScriptExecutionError("Hook script JSON must contain an 'actions' list")
        actions: list[HookScriptAction] = []
        for index, item in enumerate(actions_payload):
            if not isinstance(item, dict):
                raise HookScriptExecutionError(f"Action at index {index} is not an object")
            raw_type = item.get("type")
            if not isinstance(raw_type, str) or not raw_type.strip():
                raise HookScriptExecutionError(f"Action at index {index} is missing a valid 'type'")
            try:
                action_type = HookScriptActionType(raw_type)
            except ValueError as exc:
                raise HookScriptExecutionError(
                    f"Unsupported action type at index {index}: {raw_type}"
                ) from exc
            payload = {key: value for key, value in item.items() if key != "type"}
            actions.append(HookScriptAction(action_type=action_type, payload=payload))
        return HookScriptActionBatch(actions=actions)
