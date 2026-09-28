# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for real liveness reporting in channel handlers."""

from __future__ import annotations

import queue
from types import SimpleNamespace

import pytest

import app.atlasclaw.channels.handlers.feishu as feishu_module
from app.atlasclaw.channels.handlers.dingtalk import DingTalkHandler
from app.atlasclaw.channels.handlers.feishu import FeishuHandler
from app.atlasclaw.channels.handlers.wecom import WeComHandler
from app.atlasclaw.channels.models import ConnectionStatus


class _DeadProcess:
    """Multiprocessing process double that reports itself as dead."""

    @staticmethod
    def is_alive() -> bool:
        return False


class _AliveProcess:
    @staticmethod
    def is_alive() -> bool:
        return True


@pytest.mark.asyncio
async def test_feishu_health_check_detects_dead_sdk_process():
    """A CONNECTED status with a dead SDK subprocess must report unhealthy."""
    handler = FeishuHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._process = _DeadProcess()

    assert await handler.health_check() is False
    assert handler._status == ConnectionStatus.ERROR


@pytest.mark.asyncio
async def test_feishu_health_check_passes_with_live_process():
    handler = FeishuHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._process = _AliveProcess()

    assert await handler.health_check() is True


@pytest.mark.asyncio
async def test_dingtalk_health_check_detects_dead_sdk_process():
    handler = DingTalkHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._process = _DeadProcess()

    assert await handler.health_check() is False
    assert handler._status == ConnectionStatus.ERROR


@pytest.mark.asyncio
async def test_dingtalk_health_check_accepts_webhook_mode_without_process():
    """Webhook mode has no long-connection transport to probe."""
    handler = DingTalkHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._process = None

    assert await handler.health_check() is True


@pytest.mark.asyncio
async def test_wecom_health_check_detects_dropped_websocket():
    handler = WeComHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._ws_client = SimpleNamespace(is_connected=False)

    assert await handler.health_check() is False
    assert handler._status == ConnectionStatus.ERROR


@pytest.mark.asyncio
async def test_wecom_health_check_passes_with_live_websocket():
    handler = WeComHandler({})
    handler._status = ConnectionStatus.CONNECTED
    handler._ws_client = SimpleNamespace(is_connected=True)

    assert await handler.health_check() is True


@pytest.mark.asyncio
async def test_feishu_connect_never_assumes_connected_without_signal(monkeypatch):
    """A merely-alive SDK subprocess must not flip the status to CONNECTED.

    Historically the handler treated a process that was still alive after
    ten seconds as connected even when the SDK never signalled success.
    """
    handler = FeishuHandler({"app_id": "cli-x", "app_secret": "s"})

    async def _verified() -> bool:
        return True

    monkeypatch.setattr(handler, "_verify_credentials_for_connect", _verified)

    clock = {"now": 0.0}

    class FakeTime:
        @staticmethod
        def time() -> float:
            return clock["now"]

    monkeypatch.setattr(feishu_module, "time", FakeTime)

    async def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds + 1.0

    monkeypatch.setattr(feishu_module.asyncio, "sleep", fake_sleep)

    class FakeProcess:
        daemon = True

        def __init__(self, *args, **kwargs) -> None:
            self.pid = 4242

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            pass

        def join(self, timeout=None) -> None:
            pass

    monkeypatch.setattr(feishu_module.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(feishu_module.multiprocessing, "Queue", queue.Queue)

    result = await handler.connect()

    assert result is False
    assert handler._status == ConnectionStatus.ERROR
