# Explicit Tool Latency Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce latency for explicit tool scenarios by tightening metadata-driven tool convergence and shrinking execution/finalize prompt payloads without introducing hardcoded business routing.

**Architecture:** Extend the existing unified tool loop by adding tool-level metadata recall for all executable tools, prioritizing exact tool projection, and selecting minimal prompt payloads when the runtime has already narrowed the toolset. Keep the loop LLM-driven while reducing context volume passed into the main execution and finalize rounds.

**Tech Stack:** Python, FastAPI, PydanticAI, pytest, requests

---

### Task 1: Lock In Current Latency Baseline

**Files:**
- Modify: `docs/superpowers/specs/2026-04-11-explicit-tool-latency-reduction-design.md`

- [ ] **Step 1: Verify current baseline file exists and contains the four real-agent scenarios**

Run: `Get-Content docs\superpowers\specs\2026-04-11-explicit-tool-latency-reduction-design.md`
Expected: the file contains baseline rows for weather, CMP pending, CMP detail, and CMP services.

- [ ] **Step 2: Confirm the baseline numbers match the live measurement artifact**

Run: `Get-Content tmp_explicit_tool_latency_baseline.json`
Expected: `answer_ready_elapsed` values match the design doc baseline table.

### Task 2: Add Failing Tests For Tool-Level Metadata Recall

**Files:**
- Modify: `tests/atlasclaw/test_runner_tool_gate_behavior.py`
- Modify: `tests/atlasclaw/test_runner_tool_projection.py`

- [ ] **Step 1: Write a failing test that provider executable tools get tool-level hint docs**

```python
def test_build_tool_hint_docs_includes_provider_tools() -> None:
    tools = [
        {
            "name": "smartcmp_get_request_detail",
            "description": "Get CMP request detail by identifier",
            "source": "provider:smartcmp",
            "provider_type": "smartcmp",
            "group_ids": ["group:cmp"],
            "capability_class": "provider:smartcmp",
            "keywords": ["detail", "request detail"],
            "use_when": ["User asks for CMP request detail"],
            "avoid_when": [],
        }
    ]
    docs = _build_tool_hint_docs(tools)
    assert docs
    assert docs[0]["tool_name"] == "smartcmp_get_request_detail"
    assert docs[0]["provider_type"] == "smartcmp"
```

- [ ] **Step 2: Run the targeted test and verify it fails for the expected reason**

Run: `pytest tests/atlasclaw/test_runner_tool_gate_behavior.py -k tool_hint_docs -v`
Expected: FAIL because the current builder only covers builtins or does not expose provider tool docs.

- [ ] **Step 3: Write a failing projection test that exact tool targets win over provider-wide expansion**

```python
def test_project_minimal_toolset_prefers_exact_target_tools() -> None:
    tools = [
        {"name": "smartcmp_get_request_detail", "provider_type": "smartcmp", "capability_class": "provider:smartcmp"},
        {"name": "smartcmp_list_pending", "provider_type": "smartcmp", "capability_class": "provider:smartcmp"},
        {"name": "smartcmp_list_services", "provider_type": "smartcmp", "capability_class": "provider:smartcmp"},
    ]
    projected = project_minimal_toolset(
        available_tools=tools,
        target_tool_names=["smartcmp_get_request_detail"],
        target_provider_types=["smartcmp"],
        target_skill_names=[],
        target_capability_classes=["provider:smartcmp"],
    )
    assert [tool["name"] for tool in projected] == ["smartcmp_get_request_detail"]
```

- [ ] **Step 4: Run the targeted projection test and verify it fails**

Run: `pytest tests/atlasclaw/test_runner_tool_projection.py -k exact_target_tools -v`
Expected: FAIL because projection still keeps a broader provider subset.

### Task 3: Implement Tool-Level Recall And Exact Projection

**Files:**
- Modify: `app/atlasclaw/agent/runner_tool/runner_tool_gate_routing.py`
- Modify: `app/atlasclaw/agent/runner_tool/runner_tool_gate_policy.py`
- Modify: `app/atlasclaw/agent/runner_tool/runner_tool_projection.py`

- [ ] **Step 1: Replace the builtin-only tool hint builder with a unified executable-tool builder**

```python
def _build_tool_hint_docs(self, available_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for tool in available_tools:
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        docs.append(
            {
                "hint_id": f"tool:{name}",
                "tool_name": name,
                "tool_names": [name],
                "provider_type": str(tool.get("provider_type", "")).strip(),
                "source": str(tool.get("source", "")).strip(),
                "group_ids": list(tool.get("group_ids") or []),
                "capability_class": str(tool.get("capability_class", "")).strip(),
                "text": self._render_tool_hint_doc(tool),
            }
        )
    return docs
```

- [ ] **Step 2: Update metadata recall to prioritize tool docs over skill/provider docs**

```python
preferred_tool_names = _collect_ordered_unique(tool_hits)
if preferred_tool_names:
    preferred_provider_types = [
        provider_type
        for provider_type in preferred_provider_types
        if provider_type in tool_provider_types
    ]
```

- [ ] **Step 3: Tighten preferred tool selection so exact tool names are preserved first**

```python
if intent_plan.target_tool_names:
    explicit_names = [name for name in intent_plan.target_tool_names if name in available_by_name]
    if explicit_names:
        return explicit_names
```

- [ ] **Step 4: Re-run the two targeted tests and verify they pass**

Run: `pytest tests/atlasclaw/test_runner_tool_gate_behavior.py -k tool_hint_docs -v; pytest tests/atlasclaw/test_runner_tool_projection.py -k exact_target_tools -v`
Expected: PASS

### Task 4: Add Failing Tests For Minimal Prompt Selection

**Files:**
- Modify: `tests/atlasclaw/test_runner_prompt_context.py`
- Modify: `tests/atlasclaw/test_runner_tool_execution_contract.py`

- [ ] **Step 1: Write a failing test that explicit tool turns use minimal execution prompt when the projected toolset is small**

```python
def test_select_execution_prompt_mode_uses_minimal_for_small_explicit_toolset() -> None:
    mode = select_execution_prompt_mode(
        intent_action="use_tools",
        is_follow_up=False,
        projected_tool_count=1,
    )
    assert mode is PromptMode.MINIMAL
```

- [ ] **Step 2: Write a failing test that finalize payload only carries user question and tool evidence**

```python
def test_build_finalize_payload_is_minimal_for_tool_backed_answer() -> None:
    payload = build_finalize_payload(
        user_message="明天上海天气如何",
        tool_results=[{"tool_name": "openmeteo_weather", "result": "..."}],
    )
    assert "bootstrap" not in payload["system_prompt"].lower()
    assert "明天上海天气如何" in payload["user_prompt"]
    assert "openmeteo_weather" in payload["user_prompt"]
```

- [ ] **Step 3: Run the targeted prompt tests and verify they fail**

Run: `pytest tests/atlasclaw/test_runner_prompt_context.py -k execution_prompt_mode -v; pytest tests/atlasclaw/test_runner_tool_execution_contract.py -k finalize_payload_is_minimal -v`
Expected: FAIL because the current runtime still uses the heavier execution/finalize payloads.

### Task 5: Implement Minimal Execution And Finalize Payloads

**Files:**
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_prepare.py`
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_payload.py`
- Modify: `app/atlasclaw/agent/prompt_builder.py`
- Modify: `app/atlasclaw/agent/prompt_sections.py`
- Modify: `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py`

- [ ] **Step 1: Introduce a dedicated helper for explicit-tool execution prompt mode selection**

```python
def select_execution_prompt_mode(*, intent_action: str, is_follow_up: bool, projected_tool_count: int) -> PromptMode:
    if intent_action != "use_tools":
        return PromptMode.FULL
    if is_follow_up:
        return PromptMode.FULL
    if projected_tool_count <= 3:
        return PromptMode.MINIMAL
    return PromptMode.FULL
```

- [ ] **Step 2: Apply the helper in execution prepare so small explicit tool turns skip heavy prompt sections**

```python
prompt_mode = select_execution_prompt_mode(
    intent_action=intent_plan.action.value,
    is_follow_up=used_follow_up_context,
    projected_tool_count=len(projected_tools),
)
system_prompt = builder.build(mode=prompt_mode, ...)
```

- [ ] **Step 3: Add a minimal finalize payload builder for tool-backed answers**

```python
def build_finalize_payload(*, user_message: str, tool_results: list[dict[str, Any]]) -> dict[str, str]:
    evidence_lines = [render_tool_result_line(item) for item in tool_results]
    return {
        "system_prompt": "You are finalizing a tool-backed answer. Use only the provided tool evidence.",
        "user_prompt": (
            f"User question:\n{user_message}\n\n"
            f"Tool evidence:\n" + "\n".join(evidence_lines) + "\n\n"
            "Answer in the user language using concise markdown."
        ),
    }
```

- [ ] **Step 4: Route tool-backed finalize through the minimal payload path**

```python
finalize_payload = build_finalize_payload(
    user_message=user_message,
    tool_results=tool_result_summaries,
)
```

- [ ] **Step 5: Re-run the targeted prompt tests and verify they pass**

Run: `pytest tests/atlasclaw/test_runner_prompt_context.py -k execution_prompt_mode -v; pytest tests/atlasclaw/test_runner_tool_execution_contract.py -k finalize_payload_is_minimal -v`
Expected: PASS

### Task 6: Full Verification And Live Latency Check

**Files:**
- Modify: `docs/superpowers/specs/2026-04-11-explicit-tool-latency-reduction-design.md`

- [ ] **Step 1: Run the focused backend tests**

Run: `pytest tests/atlasclaw/test_runner_tool_gate_behavior.py tests/atlasclaw/test_runner_tool_projection.py tests/atlasclaw/test_runner_prompt_context.py tests/atlasclaw/test_runner_tool_execution_contract.py -q`
Expected: PASS

- [ ] **Step 2: Run the full backend suite**

Run: `pytest tests/atlasclaw -q`
Expected: all tests pass with no new failures.

- [ ] **Step 3: Run the committed live measurement script and write the result to tmp_explicit_tool_latency_baseline.json**

Run: `@'import json, time, requests ... see docs/superpowers/specs/2026-04-11-explicit-tool-latency-reduction-design.md baseline script body ...'@ | python -`
Expected: weather + CMP explicit tool scenarios complete with real tool calls and no timeout/failure.

- [ ] **Step 4: Compare the new numbers against baseline and record them in the design doc**

Expected: `answer_ready` and `wall time` are less than or equal to the baseline values for all four scenarios.

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/agent/runner_tool/runner_tool_gate_routing.py app/atlasclaw/agent/runner_tool/runner_tool_gate_policy.py app/atlasclaw/agent/runner_tool/runner_tool_projection.py app/atlasclaw/agent/runner_tool/runner_execution_prepare.py app/atlasclaw/agent/runner_tool/runner_execution_payload.py app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py app/atlasclaw/agent/prompt_builder.py app/atlasclaw/agent/prompt_sections.py tests/atlasclaw/test_runner_tool_gate_behavior.py tests/atlasclaw/test_runner_tool_projection.py tests/atlasclaw/test_runner_prompt_context.py tests/atlasclaw/test_runner_tool_execution_contract.py docs/superpowers/specs/2026-04-11-explicit-tool-latency-reduction-design.md docs/superpowers/plans/2026-04-11-explicit-tool-latency-reduction-implementation-plan.md
git commit -m "perf(agent): reduce explicit tool execution latency"
```

## Self-Review

- Spec coverage: this plan covers tool-level recall, exact projection, minimal execution prompt, minimal finalize payload, tests, and live latency validation.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: helper names, prompt mode selection, and projection semantics are consistent across tasks.
