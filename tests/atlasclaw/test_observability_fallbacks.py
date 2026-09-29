# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-22 regression tests: previously silent degradation is observable.

The fallback behavior of these paths is intentional, but a swallowed failure
must leave a log record with context instead of being invisible.
"""

from __future__ import annotations

import logging

import pytest

from app.atlasclaw.api.request_orchestrator import IntentRecognizer, IntentType
from app.atlasclaw.core.config import ConfigManager


class TestIntentFallbackIsLogged:
    @pytest.mark.asyncio
    async def test_llm_failure_is_logged(self, caplog):
        def _boom(prompt):
            raise RuntimeError("intent backend down")

        classifier = IntentRecognizer(llm_caller=_boom)

        with caplog.at_level(logging.ERROR, logger="app.atlasclaw.api.request_orchestrator"):
            result = await classifier.recognize("hmm let me think about that for a moment")

        assert result.intent == IntentType.GENERAL_CHAT
        assert any(
            "Intent classification failed" in record.getMessage()
            for record in caplog.records
        ), [r.getMessage() for r in caplog.records]

    @pytest.mark.asyncio
    async def test_unparseable_response_is_logged(self, caplog):
        classifier = IntentRecognizer(llm_caller=lambda prompt: "not json at all")

        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.api.request_orchestrator"):
            result = await classifier.recognize("hmm let me think about that for a moment")

        assert result.intent == IntentType.GENERAL_CHAT
        assert any(
            "Failed to parse the intent classification response" in record.getMessage()
            for record in caplog.records
        )


class TestConfigGetFailureIsLogged:
    def test_broken_lookup_logs_and_returns_default(self, tmp_path, caplog):
        manager = ConfigManager(config_path=str(tmp_path / "absent.json"))

        # Force the traversal to raise.
        class _Boom:
            def __getattr__(self, name):
                raise RuntimeError("config traversal exploded")

        manager._config = _Boom()
        manager._loaded = True

        with caplog.at_level(logging.WARNING, logger="app.atlasclaw.core.config"):
            value = manager.get("model.primary", "fallback-value")

        assert value == "fallback-value"
        assert any(
            "Failed to read configuration key" in record.getMessage()
            for record in caplog.records
        )
