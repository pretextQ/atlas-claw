# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""

hook system

for hook system, support phase:
- Gateway:gateway_start / gateway_stop
- Session:session_start / session_end
- Agent:before_agent_start / agent_end
- Prompt:before_prompt_build / agent:bootstrap
- LLM:llm_input / llm_output
- Tool:before_tool_call / after_tool_call / tool_result_persist
- Message:message_received / message_sending / message_sent
- Compaction:before_compaction / after_compaction
"""

from app.atlasclaw.hooks.system import (
    HookPhase,
    HookExecutionMode,
    HookDefinition,
    HookSystem,
)
from app.atlasclaw.hooks.runtime_models import (
    HookDecision,
    HookDecisionRecord,
    HookEventEnvelope,
    HookEventType,
    HookScriptAction,
    HookScriptActionBatch,
    HookScriptActionType,
    HookWriteMemoryRequest,
    HookContextInjection,
    PendingHookItem,
    PendingHookStatus,
)
from app.atlasclaw.hooks.runtime_store import HookStateStore
from app.atlasclaw.hooks.runtime_sinks import (
    ContextSink,
    HookMemoryWriteResult,
    MemorySink,
)
from app.atlasclaw.hooks.runtime import (
    HookHandlerDefinition,
    HookRuntime,
    HookRuntimeContext,
)
from app.atlasclaw.hooks.runtime_script import (
    HookScriptExecutionError,
    HookScriptExecutionResult,
    HookScriptHandlerDefinition,
    HookScriptRunner,
)

__all__ = [
    "HookPhase",
    "HookExecutionMode",
    "HookDefinition",
    "HookSystem",
    "HookDecision",
    "HookDecisionRecord",
    "HookEventEnvelope",
    "HookEventType",
    "HookScriptAction",
    "HookScriptActionBatch",
    "HookScriptActionType",
    "HookWriteMemoryRequest",
    "HookContextInjection",
    "PendingHookItem",
    "PendingHookStatus",
    "HookStateStore",
    "ContextSink",
    "HookMemoryWriteResult",
    "MemorySink",
    "HookHandlerDefinition",
    "HookRuntime",
    "HookRuntimeContext",
    "HookScriptExecutionError",
    "HookScriptExecutionResult",
    "HookScriptHandlerDefinition",
    "HookScriptRunner",
]
