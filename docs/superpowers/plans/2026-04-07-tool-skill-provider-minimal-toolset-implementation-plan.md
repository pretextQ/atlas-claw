# Tool/Skill/Provider Minimal Toolset Implementation Plan

> Execution protocol: implement task-by-task, run two reviews after each task, then continue.

Goal: implement the design in `docs/superpowers/specs/2026-04-07-tool-skill-provider-minimal-toolset-design.md`.

Hard constraint:
- No minimal/incomplete implementation is accepted. Every task must ship as a full implementation slice with tests.

## Task 1: Prompt Skills Compact Index

Files:
- modify: `app/atlasclaw/agent/prompt_sections.py`
- modify: `app/atlasclaw/agent/prompt_builder.py`
- modify: `tests/atlasclaw/test_md_skills.py`

Steps:
- [ ] Replace rich MD-skill XML rendering with compact index format (`name/description/file_path`).
- [ ] Add explicit read-on-demand instruction text in Skills section.
- [ ] Keep budget/limit behavior and path normalization.
- [ ] Update tests to assert compact format and instruction text.

Review A (spec compliance):
- [ ] Confirm section shape matches spec section 5.

Review B (code correctness):
- [ ] Confirm no runtime caller relies on removed XML-only fields.
- [ ] Run targeted tests:
  - `pytest tests/atlasclaw/test_md_skills.py tests/atlasclaw/test_runner_prompt_context.py -q`

## Task 2: Built-in Tool Catalog and Group Expansion

Files:
- modify: `app/atlasclaw/tools/catalog.py`
- modify: `app/atlasclaw/tools/registration.py`
- add/modify tests:
  - `tests/atlasclaw/test_md_skills.py` (existing tool catalog assertions)
  - `tests/atlasclaw/test_tool_catalog.py` (new)

Steps:
- [ ] Register existing runtime/filesystem tools: `exec`, `process`, `read`, `write`, `edit`.
- [ ] Add group map support for:
  - `group:runtime`
  - `group:fs`
  - `group:web`
  - `group:ui`
  - `group:automation`
  - `group:atlasclaw`
- [ ] Ensure group expansion ignores unregistered names safely.

Review A (spec compliance):
- [ ] Confirm section 7 built-in groups are present.

Review B (code correctness):
- [ ] Confirm profile expansion remains backward compatible.
- [ ] Run targeted tests:
  - `pytest tests/atlasclaw/test_tool_catalog.py tests/atlasclaw/test_md_skills.py -q`

## Task 3: Provider Tool Metadata and Dynamic Group Injection

Files:
- modify: `app/atlasclaw/skills/registry.py`
- modify: `app/atlasclaw/skills/md_tool_runtime.py`
- modify: `app/atlasclaw/api/deps_context.py`
- modify: `app/atlasclaw/agent/runner_prompt_context.py`
- add tests:
  - `tests/atlasclaw/test_provider_tool_groups.py`

Steps:
- [ ] Extend md-skill executable tool metadata to carry group/capability hints.
- [ ] Build provider group registry from md skill metadata (for example `group:cmp`).
- [ ] Inject `tools_snapshot` and `tool_groups_snapshot` in deps with normalized metadata.
- [ ] Normalize `group_ids/source/provider_type/capability_class` in runtime tool snapshot.

Review A (spec compliance):
- [ ] Confirm section 6 and section 7.2 metadata/group requirements are met.

Review B (code correctness):
- [ ] Confirm existing provider tools (SmartCMP/Jira) still discoverable.
- [ ] Run targeted tests:
  - `pytest tests/atlasclaw/test_provider_tool_groups.py tests/atlasclaw/test_runner_prompt_context.py -q`

## Task 4: Minimal Toolset Policy Pipeline and Runner Wiring

Files:
- add: `app/atlasclaw/tools/policy_pipeline.py`
- modify: `app/atlasclaw/agent/runner_execution.py`
- modify: `app/atlasclaw/agent/runner_prompt_context.py`
- add tests:
  - `tests/atlasclaw/test_tool_policy_pipeline.py`
  - `tests/atlasclaw/test_tool_gate_runner_integration.py` (augment)

Steps:
- [ ] Implement monotonic allow/deny pipeline with group expansion.
- [ ] Wire runner pre-loop to compute filtered minimal toolset.
- [ ] Ensure model sees filtered toolset only (unified loop retained).
- [ ] Remove residual special-case web-only path dependency in this feature path.

Review A (spec compliance):
- [ ] Confirm sections 8 and 9 behavior.

Review B (code correctness):
- [ ] Confirm provider scenario does not regress to empty toolset.
- [ ] Run targeted tests:
  - `pytest tests/atlasclaw/test_tool_policy_pipeline.py tests/atlasclaw/test_tool_gate_runner_integration.py -q`

## Task 5: Protocol Closeout (Docs + Real-Agent E2E + Timing Report)

Files:
- modify: `docs/project/tasks/2026-04-07-tool-skill-provider-minimal-toolset-plan.md`
- modify: `docs/project/state/current.md`
- optional doc updates:
  - `docs/ARCHITECTURE.MD`
  - `docs/MODULE-DETAILS.MD`

Steps:
- [ ] Update task/state progress and final outcomes.
- [ ] Run backend full test suite.
- [ ] Run real-agent E2E with required dialog:
  1) 查询 CMP 待审批列表
  2) 查询 `TIC20260316000001` 详情
  3) 查询 CMP 服务目录
- [ ] Record each turn elapsed timing and response summary.

Review A (spec compliance):
- [ ] Verify all acceptance criteria in spec section 11.

Review B (quality gate):
- [ ] Perform final standalone code review for regressions.
- [ ] Confirm test and E2E evidence is attached in report.

