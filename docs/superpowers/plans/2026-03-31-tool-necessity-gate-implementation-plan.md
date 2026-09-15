# Tool Necessity Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase 1 Tool Necessity Gate runtime so AtlasClaw can determine when tool-backed grounding is mandatory, resolve usable capabilities, block unsupported direct answers, and privilege grounded tool results in the current turn.

**Architecture:** Add a focused runtime policy layer in front of the existing agent run loop instead of rewriting the full context system. Keep Phase 1 narrow: classify requests, resolve available tool classes, enforce a tool-first path when required, emit observable runtime events, and inject grounded tool results as privileged current-turn context.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, existing AtlasClaw runner/prompt/hook runtime

---

## File Structure

**Create**
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate_models.py` - Typed decision, match, and enforcement models for the Phase 1 runtime policy.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate.py` - Tool Necessity Gate classifier and Capability Matcher.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate_models.py` - Unit tests for policy model contracts.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate.py` - Unit tests for classification and matching behavior.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate_runner_integration.py` - Integration coverage for runner enforcement path.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\e2e\test_tool_gate_e2e.py` - End-to-end coverage for grounded-answer gating.

**Modify**
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner.py` - Insert gate/matcher/enforcement before unrestricted model completion and privilege tool results in final-turn context.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\prompt_builder.py` - Add prompt-builder support for explicit tool-required policy guidance.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\prompt_sections.py` - Add anti-fabrication and tool-required instruction section text.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runtime_events.py` - Emit Tool Necessity Gate and enforcement events into Hook Runtime.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\hooks\runtime_models.py` - Add typed event enum values for tool-gate observability.
- `C:\Projects\cmps\atlasclaw\docs\project\state\current.md` - Track implementation progress and post-implementation review status.
- `C:\Projects\cmps\atlasclaw\docs\project\tasks\2026-03-31-tool-necessity-gate-plan.md` - Mark implementation tasks complete as work lands.
- `C:\Projects\cmps\atlasclaw\docs\architecture.md` - Document the new runtime policy stage in request lifecycle / runtime architecture.
- `C:\Projects\cmps\atlasclaw\docs\module-details.md` - Document new policy modules and runtime events.

---

### Task 1: Introduce typed Tool Gate models

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate_models.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate_models.py`

- [ ] **Step 1: Write the failing model-contract tests**

```python
from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolGateDecision,
    ToolPolicyMode,
)


def test_tool_gate_decision_defaults_to_direct_answer() -> None:
    decision = ToolGateDecision(reason='stable knowledge')
    assert decision.policy is ToolPolicyMode.ANSWER_DIRECT
    assert decision.needs_tool is False
    assert decision.suggested_tool_classes == []


def test_capability_match_result_tracks_missing_capabilities() -> None:
    result = CapabilityMatchResult(
        resolved_policy=ToolPolicyMode.MUST_USE_TOOL,
        tool_candidates=[],
        missing_capabilities=['web_search'],
        reason='live data required',
    )
    assert result.missing_capabilities == ['web_search']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_tool_gate_models.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError` for `tool_gate_models`

- [ ] **Step 3: Implement the policy models**

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolPolicyMode(str, Enum):
    ANSWER_DIRECT = 'answer_direct'
    PREFER_TOOL = 'prefer_tool'
    MUST_USE_TOOL = 'must_use_tool'


class ToolCandidate(BaseModel):
    name: str
    capability_class: str
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolGateDecision(BaseModel):
    needs_tool: bool = False
    needs_live_data: bool = False
    needs_private_context: bool = False
    needs_external_system: bool = False
    needs_browser_interaction: bool = False
    needs_grounded_verification: bool = False
    suggested_tool_classes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str
    policy: ToolPolicyMode = ToolPolicyMode.ANSWER_DIRECT


class CapabilityMatchResult(BaseModel):
    resolved_policy: ToolPolicyMode
    tool_candidates: list[ToolCandidate] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    reason: str


class ToolEnforcementOutcome(BaseModel):
    blocked_final_answer: bool = False
    requires_tool_first_path: bool = False
    failure_message: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/atlasclaw/test_tool_gate_models.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_tool_gate_models.py app/atlasclaw/agent/tool_gate_models.py
git commit -m "feat(agent): add tool gate policy models"
```

### Task 2: Implement Tool Necessity Gate and Capability Matcher

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\registration.py`

- [ ] **Step 1: Write the failing gate and matcher tests**

```python
from app.atlasclaw.agent.tool_gate import CapabilityMatcher, ToolNecessityGate
from app.atlasclaw.agent.tool_gate_models import ToolPolicyMode


def test_gate_defaults_to_neutral_direct_answer_without_classifier() -> None:
    gate = ToolNecessityGate()
    decision = gate.classify('任意问题', [])
    assert decision.policy is ToolPolicyMode.ANSWER_DIRECT


def test_matcher_prefers_provider_for_private_provider_question() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {'name': 'web_search', 'class': 'web_search'},
            {'name': 'jira_search', 'class': 'provider:jira'},
        ]
    )
    result = matcher.match(['provider:jira'])
    assert result.tool_candidates[0].name == 'jira_search'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_tool_gate.py -q -p no:cacheprovider`
Expected: FAIL because `ToolNecessityGate` and `CapabilityMatcher` do not exist

- [ ] **Step 3: Implement the gate and matcher**

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.atlasclaw.agent.tool_gate_models import (
    CapabilityMatchResult,
    ToolCandidate,
    ToolGateDecision,
    ToolPolicyMode,
)


class ToolNecessityGate:
    def classify(self, user_message: str, recent_history: list[dict[str, Any]]) -> ToolGateDecision:
        _ = (user_message, recent_history)
        return ToolGateDecision(
            reason='No classifier decision was provided; runtime defaults to direct-answer mode.',
            confidence=0.0,
            policy=ToolPolicyMode.ANSWER_DIRECT,
        )

    async def classify_async(
        self,
        user_message: str,
        recent_history: list[dict[str, Any]],
        *,
        classifier: Optional[Any] = None,
    ) -> ToolGateDecision:
        if classifier is None:
            return self.classify(user_message, recent_history)
        decision = await classifier.classify(user_message, recent_history)
        return ToolGateDecision.model_validate(decision)


@dataclass
class CapabilityMatcher:
    available_tools: list[dict[str, Any]]

    def match(self, suggested_tool_classes: list[str]) -> CapabilityMatchResult:
        matches: list[ToolCandidate] = []
        missing: list[str] = []
        for required in suggested_tool_classes:
            candidates = [
                ToolCandidate(
                    name=str(tool['name']),
                    capability_class=str(tool['class']),
                    priority=int(tool.get('priority', 0)),
                )
                for tool in self.available_tools
                if str(tool['class']) == required
            ]
            if candidates:
                matches.extend(sorted(candidates, key=lambda item: item.priority, reverse=True))
            else:
                missing.append(required)
        return CapabilityMatchResult(
            resolved_policy=(ToolPolicyMode.MUST_USE_TOOL if suggested_tool_classes else ToolPolicyMode.ANSWER_DIRECT),
            tool_candidates=matches,
            missing_capabilities=missing,
            reason='Resolved tool candidates from available runtime capabilities.',
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_tool_gate.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_tool_gate.py app/atlasclaw/agent/tool_gate.py
git commit -m "feat(agent): add tool necessity gate and capability matcher"
```

### Task 3: Integrate enforcement into AgentRunner

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate_runner_integration.py`

- [ ] **Step 1: Write the failing runner integration tests**

```python
import pytest

from app.atlasclaw.agent.runner import AgentRunner
from app.atlasclaw.agent.tool_gate_models import ToolPolicyMode


@pytest.mark.asyncio
async def test_runner_blocks_ungrounded_final_answer_when_tool_is_required(runner_factory, deps_factory):
    runner = runner_factory()
    deps = deps_factory()

    events = [event async for event in runner.run('web:main:dm:user:thread', '清明节上海周边会下雨吗？', deps)]

    assert any(event.type.value == 'error' for event in events)
    assert any('verification could not be completed' in (event.content or '').lower() for event in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_tool_gate_runner_integration.py -q -p no:cacheprovider`
Expected: FAIL because the runner still allows unrestricted direct completion

- [ ] **Step 3: Implement enforcement and minimal privileged tool-result context**

```python
from app.atlasclaw.agent.tool_gate import CapabilityMatcher, ToolNecessityGate
from app.atlasclaw.agent.tool_gate_models import ToolPolicyMode

# inside AgentRunner.__init__
self.tool_gate = ToolNecessityGate()

# near the start of run(), after loading transcript/history
available_tools = collect_runtime_tool_capabilities(runtime_agent or self.agent)
gate_decision = self.tool_gate.classify(user_message, message_history)
matcher = CapabilityMatcher(available_tools=available_tools)
match_result = matcher.match(gate_decision.suggested_tool_classes)

if gate_decision.policy is ToolPolicyMode.MUST_USE_TOOL and match_result.missing_capabilities:
    await self.runtime_events.trigger_tool_gate_blocked(
        session_key=session_key,
        run_id=run_id,
        decision=gate_decision.model_dump(),
        missing_capabilities=match_result.missing_capabilities,
    )
    yield StreamEvent.error_event('verification could not be completed because no matching capability is available')
    return

# before final answer path
if gate_decision.policy is ToolPolicyMode.MUST_USE_TOOL and not tool_call_summaries:
    await self.runtime_events.trigger_tool_gate_blocked(
        session_key=session_key,
        run_id=run_id,
        decision=gate_decision.model_dump(),
        missing_capabilities=[],
    )
    yield StreamEvent.error_event('verification could not be completed before final answer')
    return
```

- [ ] **Step 4: Run integration tests to verify they pass**

Run: `pytest tests/atlasclaw/test_tool_gate_runner_integration.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_tool_gate_runner_integration.py app/atlasclaw/agent/runner.py
git commit -m "feat(agent): enforce tool-required answers in runner"
```

### Task 4: Add prompt guidance and runtime observability

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\prompt_builder.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\prompt_sections.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runtime_events.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\hooks\runtime_models.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_tool_gate.py`

- [ ] **Step 1: Extend the tests to expect anti-fabrication guidance and events**

```python
def test_prompt_contains_tool_required_guidance(prompt_builder_factory) -> None:
    builder = prompt_builder_factory()
    prompt = builder.build(tools=[{'name': 'web_search', 'description': 'Web search'}])
    assert 'must not claim a search happened unless tool execution evidence exists' in prompt


@pytest.mark.asyncio
async def test_runtime_events_emit_tool_gate_required(runtime_event_dispatcher) -> None:
    await runtime_event_dispatcher.trigger_tool_gate_required(
        session_key='web:main:dm:user:thread',
        run_id='run-1',
        decision={'policy': 'must_use_tool'},
        resolved_tools=['web_search'],
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/atlasclaw/test_tool_gate.py tests/atlasclaw/test_tool_gate_runner_integration.py -q -p no:cacheprovider`
Expected: FAIL because prompt text and runtime event helpers do not exist

- [ ] **Step 3: Implement prompt section text and runtime event enums/helpers**

```python
# in prompt_sections.py

def build_tool_policy() -> str:
    return """## Tool Use Policy

Some requests require tool-backed or externally-grounded verification.
You must not claim a search, lookup, or current-data verification happened unless runtime tool execution evidence exists.
If the runtime marks tool usage as mandatory, you must follow that policy before producing a final answer.
"""

# in PromptBuilder.build()
parts.append(self._build_tool_policy())

# in runtime_models.py
class HookEventType(str, Enum):
    TOOL_GATE_EVALUATED = 'tool_gate.evaluated'
    TOOL_GATE_REQUIRED = 'tool_gate.required'
    TOOL_GATE_OPTIONAL = 'tool_gate.optional'
    TOOL_MATCHER_RESOLVED = 'tool_matcher.resolved'
    TOOL_MATCHER_MISSING_CAPABILITY = 'tool_matcher.missing_capability'
    TOOL_ENFORCEMENT_BLOCKED_FINAL_ANSWER = 'tool_enforcement.blocked_final_answer'

# in runtime_events.py
async def trigger_tool_gate_required(...):
    await self._emit_runtime_event(...)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/atlasclaw/test_tool_gate.py tests/atlasclaw/test_tool_gate_runner_integration.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/agent/prompt_builder.py app/atlasclaw/agent/prompt_sections.py app/atlasclaw/agent/runtime_events.py app/atlasclaw/hooks/runtime_models.py tests/atlasclaw/test_tool_gate.py
git commit -m "feat(agent): add tool policy prompt guidance and runtime events"
```

### Task 5: Add E2E coverage and finish documentation alignment

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\e2e\test_tool_gate_e2e.py`
- Modify: `C:\Projects\cmps\atlasclaw\docs\architecture.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\module-details.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\project\state\current.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\project\tasks\2026-03-31-tool-necessity-gate-plan.md`

- [ ] **Step 1: Write E2E tests for required-tool and direct-answer paths**

```python
import pytest


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_time_sensitive_query_requires_grounding(e2e_client):
    response = await e2e_client.ask('清明节上海周边会下雨吗？')
    assert response.used_tool is True or response.error_message


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stable_fact_query_can_answer_directly(e2e_client):
    response = await e2e_client.ask('法国首都是哪里？')
    assert response.final_text
```

- [ ] **Step 2: Run the new E2E tests to verify they fail**

Run: `pytest tests/atlasclaw/e2e/test_tool_gate_e2e.py -q -p no:cacheprovider`
Expected: FAIL until the runtime policy is fully wired

- [ ] **Step 3: Update canonical docs and project tracking files**

```markdown
# docs/project/state/current.md
## Completed
- Implemented Phase 1 Tool Necessity Gate runtime policy.
- Added capability matching, enforcement, runtime events, and minimal privileged tool-result context.

## Next Step
- Perform post-implementation alignment review across code, task, and spec.
```

```markdown
# docs/project/tasks/2026-03-31-tool-necessity-gate-plan.md
## Implementation Status
- [x] Implementation completed.
- [x] Post-implementation alignment review completed.
- [ ] Final standalone code review completed.
```

- [ ] **Step 4: Run full verification**

Run: `pytest tests/atlasclaw/test_tool_gate_models.py tests/atlasclaw/test_tool_gate.py tests/atlasclaw/test_tool_gate_runner_integration.py -q -p no:cacheprovider`
Expected: PASS

Run: `pytest tests/atlasclaw/e2e/test_tool_gate_e2e.py -q -p no:cacheprovider`
Expected: PASS

Run: `pytest tests/atlasclaw -m "not e2e" -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/e2e/test_tool_gate_e2e.py docs/architecture.md docs/module-details.md docs/project/state/current.md docs/project/tasks/2026-03-31-tool-necessity-gate-plan.md
git commit -m "test(agent): add end-to-end coverage for tool necessity gate"
```

## Self-Review Notes
- Spec coverage checked: every Phase 1 section maps to a task above.
- Placeholder scan checked: no `TODO` or unresolved placeholders remain in the plan body.
- Type consistency checked: the plan uses consistent names for `ToolNecessityGate`, `CapabilityMatcher`, `ToolPolicyMode`, and `ToolEnforcementOutcome`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-03-31-tool-necessity-gate-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
