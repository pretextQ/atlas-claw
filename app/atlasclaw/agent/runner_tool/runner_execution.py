# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.agent.runner_tool.runner_execution_loop import RunnerExecutionLoopMixin
from app.atlasclaw.agent.runner_tool.runner_execution_payload import RunnerExecutionPayloadMixin
from app.atlasclaw.agent.runner_tool.runner_execution_retry import RunnerExecutionRetryMixin
from app.atlasclaw.agent.runner_tool.runner_execution_runtime import RunnerExecutionRuntimeMixin
from app.atlasclaw.agent.runner_tool.runner_execution_toolset import RunnerExecutionToolsetMixin


class RunnerExecutionMixin(
    RunnerExecutionLoopMixin,
    RunnerExecutionRetryMixin,
    RunnerExecutionRuntimeMixin,
    RunnerExecutionToolsetMixin,
    RunnerExecutionPayloadMixin,
):
    """Composite mixin for agent runtime execution orchestration."""

    pass
