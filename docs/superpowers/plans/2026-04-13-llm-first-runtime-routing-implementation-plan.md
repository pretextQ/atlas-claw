# LLM-First Runtime Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AtlasClaw's planner-centric routing path with a single main-model runtime path that selects turn intent from a compact capability index, preserves follow-up context, and verifies artifact completion before finalizing.

**Architecture:** Remove the dedicated planner from the main request path. The new flow prepares recent conversation context plus a unified capability index, lets the main model choose `direct_answer` / `ask_clarification` / `use_tools` / `create_artifact` in the main request itself, and then expands only the selected capabilities for continuation. Existing runtime guards stay in place after that decision, but metadata recall becomes candidate compression instead of a routing authority.

**Tech Stack:** Python, PydanticAI, pytest

---

### Task 1: Remove Planner-Centric Configuration And Add Index Limits

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\core\config_schema.py`
- Modify: `C:\Projects\cmps\atlasclaw\atlasclaw.json`
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw.test.json`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_gate_behavior.py`

- [ ] Remove `agent.routing_mode` style assumptions from the main runtime path.
- [ ] Add optional index-size controls for visible skills/tools, such as `agent.max_indexed_skills` and `agent.max_indexed_tools`.
- [ ] Add an optional character-budget control such as `agent.max_capability_index_chars`.
- [ ] Keep schema defaults backward-compatible for deployments that still carry planner-era config values.
- [ ] Add a config test proving planner-specific settings no longer control the main routing path.

### Task 2: Replace The Main Routing Entry

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_llm_routing.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_prepare.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_runtime.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_gate_behavior.py`

- [ ] Introduce a single main routing path that is always used for normal agent requests.
- [ ] Define a strict turn-intent result contract with only `direct_answer`, `ask_clarification`, `use_tools`, and `create_artifact`.
- [ ] Remove any dedicated planner-model call from the hot path.
- [ ] Add tests that the new path is now the default request path.
- [ ] Add a regression test that the main path no longer emits the current `tool_intent_plan_model_start` / timeout sequence.

### Task 3: Build The Capability Index Prompt Surface

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_prompt_context.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\prompt_sections.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\skills\registry.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\registration.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_prompt_context.py`

- [ ] Add a unified `capability_index` adapter that merges executable skills, markdown skills, and tools into one compact prompt surface.
- [ ] Include stable `capability_id`, short summary, provider scope, artifact types, and markdown-skill path data where applicable.
- [ ] Run tool policy filtering, skill invocation eligibility checks, and provider/group/plugin visibility checks before building the index.
- [ ] Ensure the prompt can expose the index without dumping every skill body.
- [ ] Deduplicate markdown skills and their declared tool names so the same capability is not shown twice with different wording.
- [ ] Add stable truncation behavior with count/char diagnostics so oversized indexes are predictable and observable.
- [ ] Add tests that artifact-oriented skills like `powerpoint-pptx-1.0.1` are visible to the model even when the query is Chinese.

### Task 4: Preserve Follow-Up Context In The Main Path

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_prepare.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_routing.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_execution_payload.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_prompt_context.py`

- [ ] Stop clearing runtime history just because a turn may use tools.
- [ ] Ensure follow-up references like `这些申请`, `上面的结果`, and `刚才那个列表` retain recent conversation context.
- [ ] Add tests that a second-turn artifact request sees the prior data-bearing answer.
- [ ] Keep existing trimming / compaction protections for oversized histories.

### Task 5: Demote Metadata Recall To Candidate Compression

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_routing.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_model.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_tool_gate_policy.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_gate_behavior.py`

- [ ] Keep metadata recall output, but remove its ability to directly short-circuit action selection.
- [ ] Allow metadata recall to shrink the visible candidate set when the set is too large.
- [ ] Preserve provider/private-data hints without letting them override artifact intent or follow-up intent.
- [ ] Add tests that SmartCMP metadata can bias candidates without forcing `use_tools` for `将这些申请写入一个新的PPT`.

### Task 6: Implement Main-Model Turn Decision And Stage-Two Expansion

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_llm_turn_decision.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_runtime.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_stream.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`

- [ ] Define the model-facing decision schema for the four allowed actions.
- [ ] Parse the returned decision before entering tool execution.
- [ ] Apply explicit runtime constraints before model routing, including `no tools`, `must use a tool`, or `specific capability/tool` constraints where provided by API or turn state.
- [ ] Require `selected_capability_ids` for `use_tools` and `create_artifact`, so runtime knows exactly what to expand in stage two.
- [ ] Add a second-stage expansion path: selected tool ids expose minimal tools, selected skill ids load only that skill and its bound tools.
- [ ] Ensure continuation is evidence-driven: after each tool result, the model decides whether to answer, ask, create an artifact, or call another tool; runtime must not pre-plan a fixed tool queue.
- [ ] Reject invalid tool-style drafts when the action is `direct_answer` or `ask_clarification`.
- [ ] Keep `direct_answer` and `ask_clarification` to a single main-model call unless a retry is required for invalid markup recovery.

### Task 7: Add Artifact-Aware Completion Checks

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_post.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_payload.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`

- [ ] Add a goal-completion classifier for artifact requests like PPT, file, report, and markdown export.
- [ ] Prevent `tool_only_finalize` from treating plain lookup results as success for artifact goals.
- [ ] Reuse same-turn recovery when tools returned data but never reached the requested artifact path.
- [ ] Add tests that SmartCMP list results alone do not satisfy a PPT-generation request.

### Task 8: Keep Runtime Guards After Planner Removal

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_stream.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_post.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_prepare.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_gate_behavior.py`

- [ ] Keep timeout / repeated-loop / no-progress protections active after planner removal.
- [ ] Preserve `before_tool_call` style validation, block, and param-adjustment hooks on the new main path.
- [ ] Keep loop detection based on argument signatures, result signatures, and ping-pong detection instead of simple call counts.
- [ ] Ensure recovery messages explain whether the runtime is switching to direct answer, clarification, or artifact fallback.
- [ ] Preserve the private-data safeguard: the model must not fabricate provider data when tools fail.
- [ ] Run regression tests for repeated tool loop handling and timeout fallback.

### Task 9: Stream Every LLM Loop Explicitly

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_flow_stream.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner_tool\runner_execution_runtime.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runtime_events.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runtime_event_dispatcher.py`

- [ ] Emit an explicit streamed status before every LLM entry, including the initial request and each continuation after tool results.
- [ ] Include loop metadata such as `loop_index` and `loop_reason` so frontend and logs can distinguish `initial_request` from `tool_result_continuation` and recovery loops.
- [ ] Add dedicated continuation messages like `Tool results received. Continuing reasoning with tool evidence.` instead of reusing vague generic statuses.
- [ ] Ensure `llm_input` / `llm_output` trace hooks still fire on every LLM re-entry, not just the first loop.
- [ ] Add tests that multi-tool runs produce visible streamed statuses for each LLM re-entry, not just the first one.

### Task 10: Add Chinese Multi-Turn E2E Coverage

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\e2e\test_runtime_routing.py`
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_gate_behavior.py`
- Modify: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_runner_tool_execution_contract.py`

- [ ] Add an end-to-end case for `查一个 cmp 所有待审批的申请` followed by `将这些申请写入一个新的PPT`.
- [ ] Add an end-to-end case for `我想查下上海周边的骑行公园` where direct answer is allowed.
- [ ] Add a follow-up export case like `把上面的结果保存成 markdown`.
- [ ] Verify the new main path covers these cases without planner fallback.
- [ ] Verify at least one multi-tool E2E case shows explicit streamed statuses on every `LLM -> tool -> LLM` continuation.
- [ ] Verify session/tool-result pairing remains consistent across continuation loops and does not finalize early.

### Task 11: Update Canonical Docs And Delete Planner Assumptions

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\docs\architecture.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\module-details.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\development-spec.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\superpowers\specs\2026-04-13-llm-first-runtime-routing-design.md`

- [ ] Document the new routing order and decision ownership.
- [ ] Document that metadata recall is no longer a routing short-circuit.
- [ ] Document artifact-aware completion checks and follow-up context preservation.
- [ ] Remove planner-centric assumptions from the canonical docs.

---

## Verification Sequence

- [ ] Run targeted unit tests for routing, prompt context, and post-phase completion checks.
- [ ] Run targeted unit tests for continuation-loop streaming visibility.
- [ ] Run the new Chinese multi-turn E2E suite.
- [ ] Run the existing regression suites for tool loops, direct-answer recovery, and provider-private-data fallback.
- [ ] Capture residual risks after planner removal.

---

## Expected Deliverables

- A planner-free main routing path
- Readable capability indexes in the prompt
- Default follow-up context retention
- Metadata recall reduced to hinting / compression
- Artifact-aware completion checks
- Explicit streamed statuses for every LLM loop re-entry
- Chinese multi-turn E2E regression coverage
