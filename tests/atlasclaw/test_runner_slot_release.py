# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for agent-pool slot release resilience under early consumer exit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.atlasclaw.agent.agent_pool import AgentInstancePool
from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.runner_tool.runner_execution_runtime import RuntimeAgentSlot
from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.core.deps import SkillDeps
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool
from app.atlasclaw.auth.models import UserInfo


SESSION_KEY = "agent:main:user:u1:main"


def _token() -> TokenEntry:
    return TokenEntry(
        token_id="tok-1",
        provider="kimi",
        model="test-model",
        base_url="https://llm.example.com",
        api_key="sk-test",
        context_window=8000,
    )


class TestRuntimeAgentSlot:
    def test_release_is_idempotent(self):
        calls: list[int] = []
        slot = RuntimeAgentSlot(lambda: calls.append(1))
        slot.release()
        slot.release()
        slot()
        assert calls == [1]


class TestEarlyConsumerExitReleasesSlot:
    def _runner(self, pool: AgentInstancePool) -> AgentRunner:
        token_pool = TokenPool()
        token_pool.register_token(_token())
        policy = DynamicTokenPolicy(token_pool, strategy="health")
        return AgentRunner(
            agent=SimpleNamespace(),
            session_manager=SimpleNamespace(),
            token_policy=policy,
            agent_pool=pool,
            agent_factory=lambda agent_id, token: SimpleNamespace(),
        )

    def test_breaking_out_after_acquire_restores_the_permit(self):
        pool = AgentInstancePool(max_concurrent_per_instance=1)
        runner = self._runner(pool)
        deps = SkillDeps(user_info=UserInfo(user_id="u1"), extra={})

        async def scenario() -> None:
            generator = runner.run(SESSION_KEY, "hello", deps)
            async for event in generator:
                if event.type == "error":
                    break
            await generator.aclose()

        asyncio.run(scenario())

        instance = pool.get("main", "tok-1")
        assert instance is not None
        # The context-window guard (token context_window=8000 < 16000) stops
        # the run right after the slot was acquired; an early consumer break
        # must not leak the permit.
        assert instance.concurrency_sem._value == 1

    def test_natural_completion_restores_the_permit(self):
        pool = AgentInstancePool(max_concurrent_per_instance=1)
        runner = self._runner(pool)
        deps = SkillDeps(user_info=UserInfo(user_id="u1"), extra={})

        async def scenario() -> None:
            generator = runner.run(SESSION_KEY, "hello", deps)
            async for _ in generator:
                pass

        asyncio.run(scenario())

        instance = pool.get("main", "tok-1")
        assert instance is not None
        assert instance.concurrency_sem._value == 1


@pytest.mark.asyncio
async def test_natural_completion_also_restores_the_permit():
    """The un-marked variants above run via asyncio.run; keep one marked test too."""
    pool = AgentInstancePool(max_concurrent_per_instance=1)
    token_pool = TokenPool()
    token_pool.register_token(_token())
    policy = DynamicTokenPolicy(token_pool, strategy="health")
    runner = AgentRunner(
        agent=SimpleNamespace(),
        session_manager=SimpleNamespace(),
        token_policy=policy,
        agent_pool=pool,
        agent_factory=lambda agent_id, token: SimpleNamespace(),
    )
    deps = SkillDeps(user_info=UserInfo(user_id="u1"), extra={})

    generator = runner.run(SESSION_KEY, "hello", deps)
    async for _ in generator:
        pass

    instance = pool.get("main", "tok-1")
    assert instance is not None
    assert instance.concurrency_sem._value == 1
