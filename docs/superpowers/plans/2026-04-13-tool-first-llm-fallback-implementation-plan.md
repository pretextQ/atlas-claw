# Tool-First LLM Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tool use capability-driven and allow same-turn LLM fallback whenever a tool-first turn yields no usable evidence.

**Architecture:** Keep the unified tool loop intact, but relax the gate so public realtime questions are not auto-forced into tools. When a tool-first turn ends with empty/error/no-progress evidence, run a minimal fallback finalization pass that uses tool snapshots plus failure context to produce a safe final answer.

**Tech Stack:** Python, PydanticAI, pytest

---

### Task 1: Lock In The New Behavior With Failing Contract Tests

**Files:**
- Modify: `tests/atlasclaw/test_runner_tool_execution_contract.py`
- Modify: `tests/atlasclaw/test_runner_tool_gate_behavior.py`

- [ ] **Step 1: Add a failing post-phase test for repeated no-progress search fallback**
- [ ] **Step 2: Add a failing post-phase test for tool error fallback**
- [ ] **Step 3: Add a failing prompt test that public realtime requests are no longer described as default must-use-tool**
- [ ] **Step 4: Run the targeted tests and confirm they fail for the expected reasons**

### Task 2: Relax Tool Gate Policy To Capability Match First

**Files:**
- Modify: `app/atlasclaw/agent/runner_tool/runner_tool_gate_model.py`

- [ ] **Step 1: Rewrite classifier prompt rubric away from freshness-first enforcement**
- [ ] **Step 2: Stop normalizing `needs_live_data` into automatic strict tool enforcement**
- [ ] **Step 3: Keep provider/skill/private-context routing strong without reintroducing mandatory web verification**

### Task 3: Add Same-Turn LLM Fallback For Tool Failure

**Files:**
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_payload.py`
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py`

- [ ] **Step 1: Add a dedicated fallback payload builder for failed/empty tool turns**
- [ ] **Step 2: Implement a helper that calls the model to produce a safe fallback answer**
- [ ] **Step 3: Trigger that helper from post phase for empty/error/no-progress/repeated-failure turns**
- [ ] **Step 4: Preserve tool-only finalize when real tool evidence is already sufficient**

### Task 4: Surface Clear Runtime Status During Fallback

**Files:**
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py`

- [ ] **Step 1: Emit a warning that tool evidence was insufficient**
- [ ] **Step 2: Emit a reasoning status that the runtime is switching to model fallback**
- [ ] **Step 3: Ensure successful fallback ends in `answered`, not `failed`**

### Task 5: Verify Regressions

**Files:**
- Modify: `tests/atlasclaw/test_runner_tool_execution_contract.py`
- Modify: `tests/atlasclaw/test_runner_tool_gate_behavior.py`

- [ ] **Step 1: Run targeted contract and gate tests**
- [ ] **Step 2: Run the existing timeout / tool-only fallback regression tests**
- [ ] **Step 3: Summarize any residual risk, especially for provider/private requests where fallback must not fabricate missing data**
