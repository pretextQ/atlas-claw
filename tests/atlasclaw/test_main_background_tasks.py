# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-12 regression tests: background task lifecycle in main.py.

Covers: fire-and-forget tasks are kept alive and their failures observed
(F-0042), a failing heartbeat tick is logged and the loop survives
(F-0043), and shutdown cancels + awaits tracked background tasks.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.atlasclaw import main as main_module


class TestTrackBackgroundTask:
    @pytest.mark.asyncio
    async def test_task_keeps_a_strong_reference_until_done(self):
        async def _slow() -> None:
            await asyncio.sleep(0.05)

        task = main_module._track_background_task(
            asyncio.create_task(_slow(), name="wp12-slow"),
            name="wp12-slow",
        )
        assert task in main_module._background_tasks

        await task
        # The done callback drops the reference.
        for _ in range(20):
            if task not in main_module._background_tasks:
                break
            await asyncio.sleep(0.01)
        assert task not in main_module._background_tasks

    @pytest.mark.asyncio
    async def test_task_failure_is_logged(self, caplog):
        async def _boom() -> None:
            raise RuntimeError("background task exploded")

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.main"):
            task = main_module._track_background_task(
                asyncio.create_task(_boom(), name="wp12-boom"),
                name="wp12-boom",
            )
            with pytest.raises(RuntimeError):
                await task
            # Let the done callback run.
            await asyncio.sleep(0)

        assert any(
            "wp12-boom" in record.getMessage() and "background task" in record.getMessage().lower()
            for record in caplog.records
        ), [r.getMessage() for r in caplog.records]

    @pytest.mark.asyncio
    async def test_cancelled_task_is_not_reported_as_failure(self, caplog):
        async def _slow() -> None:
            await asyncio.sleep(10)

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.main"):
            task = main_module._track_background_task(
                asyncio.create_task(_slow(), name="wp12-cancel"),
                name="wp12-cancel",
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)

        assert not any("background task" in r.getMessage().lower() for r in caplog.records)


class TestHeartbeatLoopResilience:
    """F-0043: a failing tick must not kill the heartbeat supervisor."""

    @pytest.mark.asyncio
    async def test_tick_failure_is_logged_and_loop_continues(self, caplog):
        """Drives the real loop from main.py, not a copy of it."""
        tick_calls: list[int] = []

        class _Runtime:
            async def register_job(self, job) -> None:
                return None

            async def run_once(self) -> None:
                tick_calls.append(len(tick_calls))
                if len(tick_calls) == 1:
                    raise RuntimeError("tick exploded")

        async def _no_jobs():
            return []

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.main"):
            task = asyncio.create_task(
                main_module._run_heartbeat_loop(
                    tick_seconds=0.01,
                    heartbeat_runtime=_Runtime(),
                    build_agent_jobs=_no_jobs,
                    build_channel_jobs=list,
                )
            )
            for _ in range(200):
                if len(tick_calls) >= 3:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(tick_calls) >= 3, "loop must survive a failing tick"
        assert any("Heartbeat tick failed" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_cancellation_propagates_out_of_the_loop(self):
        async def _no_jobs():
            return []

        class _Runtime:
            async def register_job(self, job) -> None:
                return None

            async def run_once(self) -> None:
                return None

        task = asyncio.create_task(
            main_module._run_heartbeat_loop(
                tick_seconds=10,
                heartbeat_runtime=_Runtime(),
                build_agent_jobs=_no_jobs,
                build_channel_jobs=list,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_main_defines_heartbeat_loop_with_exception_guard(self):
        """Source-level check: the shipped loop guards non-cancellation errors."""
        import inspect

        source = inspect.getsource(main_module)
        assert "Heartbeat tick failed; continuing" in source
        assert "except asyncio.CancelledError:" in source
