# Context Management Alignment Plan (AtlasClaw vs OpenClaw)

## Scope
- Compare AtlasClaw and OpenClaw context-management implementations at architecture and runtime levels.
- Define the target alignment boundaries for AtlasClaw (must-align / optional / out-of-scope).
- Produce an implementation-ready design direction before any code refactor.

## Steps
1. [x] Baseline comparison (success criteria: concrete file-level diff map for guard, pruning, compaction, prompt/bootstrap, memory tools, session/transcript)
2. [x] Parity target selection (success criteria: user-approved target level: strict parity / pragmatic parity / selective parity)
3. [x] Design options and trade-offs (success criteria: 2-3 options with recommendation and risks)
4. [x] Final design alignment review (success criteria: state/task/spec terminology and scope aligned; no contradictory boundaries)
5. [x] Implementation plan handoff (success criteria: detailed executable plan with tests and rollout checkpoints)

## Verification
- command: compare `app/atlasclaw/agent|session|memory` with `openclaw-cn/src/agents|config/sessions|memory`; write spec; run self-review for placeholders/contradictions/scope drift; cross-check state/task/spec consistency
- expected: a complete, user-reviewable context-alignment spec with explicit acceptance criteria and phased rollout
- actual: completed. Spec written at `docs/superpowers/specs/2026-04-04-context-management-alignment-design.md`; parity mode fixed to pragmatic alignment; state/task/spec terminology aligned

## Handoff Notes
- No implementation changes are included in this plan file.
- This plan is the collaboration protocol entrypoint for the next context-management refactor cycle.
- Implementation plan written at `docs/superpowers/plans/2026-04-05-context-management-alignment-implementation-plan.md`.
- Execution mode selected: inline execution (user requested to continue without pausing).

## Implementation Execution Status (2026-04-05)
- [x] Task 1 complete: context window guard module integrated into runner warn/block path.
- [x] Task 2 complete: session-aware prompt context resolver integrated into prompt builder with per-file and total budgets.
- [x] Task 2 enhancement complete: bootstrap budgets now scale dynamically with resolved runtime context window and remain capped by configured limits.
- [x] Task 3 complete: runtime context pruning integrated into runner; compaction safeguard integrated into compaction summary pipeline.
- [x] Task 4 complete: memory search/get tools now return structured citation fields (`path`, `start_line`, `end_line`, `citation`) in `details`.
- [x] Task 5 complete: session manager now includes transcript cache (mtime/size invalidation), transient read retry, and archive budget cleanup.
- [x] Task 6 complete: final docs reconciliation + final regression summary refreshed (commit packaging pending user confirmation).
- [x] Task 7 complete: bootstrap file cache (mtime/size invalidation) and compaction safeguard enhancement (staged summarization + adaptive history-share pruning).
- [x] Task 8 complete: compaction fail-safe preserves original transcript when summarization fails.
- [x] Task 9 complete: context pruning runtime now supports OpenClaw-style config layer (`mode/ttl/tools allow-deny`) and runner TTL gating.
- [x] Task 10 complete: compaction safeguard now appends workspace critical rules (`Session Startup`/`Red Lines`) from `AGENTS.md` during summary generation.
- [x] Task 11 complete: Round-2 OpenClaw gap-closure audit documented with prioritized P0/P1 list (`docs/project/tasks/2026-04-06-openclaw-context-gap-audit.md`).

## Implementation Verification Snapshot
- command: `pytest tests/atlasclaw/test_context_window_guard.py tests/atlasclaw/test_prompt_context_resolver.py tests/atlasclaw/test_context_pruning.py tests/atlasclaw/test_memory_tool_citations.py tests/atlasclaw/session/test_session_manager_governance.py -q`
- expected: all context-alignment task tests pass
- actual: `18 passed`
- command: `pytest tests/atlasclaw/test_compaction_alignment.py tests/atlasclaw/test_prompt_context_resolver.py -q`
- expected: bootstrap cache and compaction safeguard alignment tests pass
- actual: `10 passed`
- command: `pytest tests/atlasclaw/test_context_pruning.py tests/atlasclaw/test_core.py -q`
- expected: pruning config-layer behavior and schema integration tests pass
- actual: `22 passed`
- command: `pytest tests/atlasclaw/test_compaction_alignment.py tests/atlasclaw/test_context_pruning.py -q`
- expected: compaction workspace-rule safeguard and pruning runtime tests pass
- actual: `13 passed`
- command: `pytest tests/atlasclaw -q`
- expected: full backend suite passes
- actual: `986 passed, 8 skipped`; no blocking failure remains. `e2e_api` now auto-skips when TEST_SERVER_URL is unreachable.
