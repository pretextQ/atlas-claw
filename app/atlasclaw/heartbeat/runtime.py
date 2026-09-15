# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import inspect
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo
from uuid import uuid4

from app.atlasclaw.heartbeat.models import (
    HeartbeatEventType,
    HeartbeatEventEnvelope,
    HeartbeatJobDefinition,
    HeartbeatJobStateSnapshot,
    HeartbeatJobType,
)
from app.atlasclaw.heartbeat.store import HeartbeatStateStore
from app.atlasclaw.heartbeat.targets import HeartbeatTargetResolver


@dataclass
class HeartbeatRuntimeContext:
    """Dependencies required by the heartbeat runtime."""

    store: HeartbeatStateStore
    agent_executor: Any
    channel_executor: Any
    emit_event: Optional[Callable[[HeartbeatEventEnvelope], Optional[Awaitable[None]] | None]] = None
    max_concurrent_jobs: int = 16
    emit_runtime_events: bool = True
    persist_local_event_log: bool = True


class HeartbeatRuntime:
    """Unified runtime that schedules and executes typed heartbeat jobs."""

    def __init__(self, context: HeartbeatRuntimeContext):
        self.context = context
        self._jobs: dict[str, HeartbeatJobDefinition] = {}
        self._state: dict[str, HeartbeatJobStateSnapshot] = {}
        self._target_resolver = HeartbeatTargetResolver()
        self._loaded_users: set[str] = set()
        self._running_jobs: set[str] = set()

    def register_job(self, job: HeartbeatJobDefinition) -> None:
        self._ensure_user_state_loaded(job.owner_user_id)
        self._jobs[job.job_id] = job
        self._persist_jobs_for_user(job.owner_user_id)

    def get_job_state(self, job_id: str) -> Optional[HeartbeatJobStateSnapshot]:
        return self._state.get(job_id)

    async def run_once(self) -> None:
        now = datetime.now(timezone.utc)
        due_jobs: list[HeartbeatJobDefinition] = []
        for job in self._jobs.values():
            if not job.enabled:
                continue
            if job.job_id in self._running_jobs:
                continue
            if not self._is_within_active_hours(job, now):
                continue
            snapshot = self._state.get(job.job_id)
            if snapshot and snapshot.next_run_at and snapshot.next_run_at > now:
                continue
            due_jobs.append(job)

        semaphore = asyncio.Semaphore(max(1, self.context.max_concurrent_jobs))
        await asyncio.gather(*(self._run_job(job, now, semaphore) for job in due_jobs))

    async def _execute_job(self, job: HeartbeatJobDefinition) -> Any:
        if job.job_type == HeartbeatJobType.AGENT_TURN:
            return await self.context.agent_executor.execute(job)
        if job.job_type == HeartbeatJobType.CHANNEL_CONNECTION:
            return await self.context.channel_executor.execute(job)
        raise ValueError(f"Unsupported heartbeat job type: {job.job_type}")

    async def _emit_job_event(self, job: HeartbeatJobDefinition, result: Any, created_at: datetime) -> None:
        if job.job_type == HeartbeatJobType.AGENT_TURN:
            event_type = (
                HeartbeatEventType.AGENT_COMPLETED
                if result.status == "healthy"
                else HeartbeatEventType.AGENT_FAILED
            )
        else:
            if result.status == "healthy":
                event_type = HeartbeatEventType.CHANNEL_CHECK_SUCCEEDED
            elif result.status == "degraded":
                event_type = HeartbeatEventType.CHANNEL_DEGRADED
            else:
                event_type = HeartbeatEventType.CHANNEL_CHECK_FAILED

        await self._emit_event(
            event_type,
            job,
            created_at,
            payload=dict(getattr(result, "context_payload", {}) or {}),
        )

    async def _run_job(
        self,
        job: HeartbeatJobDefinition,
        now: datetime,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            self._running_jobs.add(job.job_id)
            try:
                await self._emit_event(self._started_event_type_for(job), job, now, payload={})
                result = await self._execute_job(job)
                resolved_target = (
                    self._target_resolver.resolve(job.target)
                    if job.target is not None
                    else None
                )
                if resolved_target is not None:
                    result_payload = dict(getattr(result, "context_payload", {}) or {})
                    result_payload["target"] = resolved_target.to_dict()
                    result.context_payload = result_payload
                next_delay = int(getattr(result, "next_delay_seconds", 0) or job.every_seconds or 60)
                previous = self._state.get(job.job_id)
                updated = HeartbeatJobStateSnapshot(
                    job_id=job.job_id,
                    job_type=job.job_type,
                    status=result.status,
                    consecutive_failures=(
                        result.consecutive_failures
                        if hasattr(result, "consecutive_failures")
                        else 0
                    ),
                    last_error=getattr(result, "error", ""),
                    last_result_summary=result.result_summary,
                    last_run_at=now,
                    last_success_at=(
                        now
                        if result.status == "healthy"
                        else (previous.last_success_at if previous is not None else None)
                    ),
                    last_failure_at=(
                        previous.last_failure_at
                        if result.status == "healthy" and previous is not None
                        else (None if result.status == "healthy" else now)
                    ),
                    next_run_at=now + timedelta(seconds=max(1, next_delay)),
                    last_target_resolution=(
                        resolved_target.to_dict() if resolved_target is not None else {}
                    ),
                    last_delivery_result={
                        "should_notify": bool(
                            getattr(result, "should_notify", False)
                            or getattr(result, "should_alert", False)
                        )
                    },
                )
                self._state[job.job_id] = updated
                self.context.store.save_state(job.owner_user_id, self._state_for_user(job.owner_user_id))
                await self._emit_job_event(job, result, now)
                for extra_event_type in getattr(result, "extra_event_types", []):
                    await self._emit_event(
                        HeartbeatEventType(extra_event_type),
                        job,
                        now,
                        payload=dict(getattr(result, "context_payload", {}) or {}),
                    )
            finally:
                self._running_jobs.discard(job.job_id)

    async def _emit_event(
        self,
        event_type: HeartbeatEventType,
        job: HeartbeatJobDefinition,
        created_at: datetime,
        *,
        payload: dict[str, Any],
    ) -> None:
        event = HeartbeatEventEnvelope(
            id=f"heartbeat-event-{uuid4().hex}",
            event_type=event_type,
            job_id=job.job_id,
            job_type=job.job_type,
            user_id=job.owner_user_id,
            created_at=created_at,
            channel=str(payload.get("target", {}).get("channel") or payload.get("metadata", {}).get("channel_type") or ""),
            account_id=str(payload.get("target", {}).get("account_id") or payload.get("metadata", {}).get("connection_id") or ""),
            session_key=str(payload.get("metadata", {}).get("session_key") or payload.get("target", {}).get("session_key") or ""),
            run_id=str(payload.get("metadata", {}).get("run_id") or f"heartbeat-{job.job_id}"),
            payload=payload,
        )
        if self.context.persist_local_event_log:
            self.context.store.append_event(job.owner_user_id, event)
        if not self.context.emit_runtime_events or self.context.emit_event is None:
            return
        emitted = self.context.emit_event(event)
        if inspect.isawaitable(emitted):
            await emitted

    def _started_event_type_for(self, job: HeartbeatJobDefinition) -> HeartbeatEventType:
        if job.job_type == HeartbeatJobType.AGENT_TURN:
            return HeartbeatEventType.AGENT_STARTED
        return HeartbeatEventType.CHANNEL_CHECK_STARTED

    def _is_within_active_hours(self, job: HeartbeatJobDefinition, now: datetime) -> bool:
        if not job.active_hours_start or not job.active_hours_end:
            return True
        try:
            zone = ZoneInfo(job.active_hours_timezone or "UTC")
            local_now = now.astimezone(zone).time()
            start = time.fromisoformat(job.active_hours_start)
            end = time.fromisoformat(job.active_hours_end)
        except Exception:
            return True
        if start <= end:
            return start <= local_now <= end
        return local_now >= start or local_now <= end

    def _persist_jobs_for_user(self, user_id: str) -> None:
        self.context.store.save_jobs(user_id, self._jobs_for_user(user_id))

    def _ensure_user_state_loaded(self, user_id: str) -> None:
        if user_id in self._loaded_users:
            return
        for snapshot in self.context.store.load_state(user_id):
            self._state.setdefault(snapshot.job_id, snapshot)
        self._loaded_users.add(user_id)

    def _jobs_for_user(self, user_id: str) -> list[HeartbeatJobDefinition]:
        return [job for job in self._jobs.values() if job.owner_user_id == user_id]

    def _state_for_user(self, user_id: str) -> list[HeartbeatJobStateSnapshot]:
        user_job_ids = {job.job_id for job in self._jobs_for_user(user_id)}
        return [snapshot for job_id, snapshot in self._state.items() if job_id in user_job_ids]
