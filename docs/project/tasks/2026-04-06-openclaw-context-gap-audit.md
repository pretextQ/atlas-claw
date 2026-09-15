# OpenClaw Context Alignment Gap Audit (Round-2)

## Scope
- Compare AtlasClaw current context-management implementation with OpenClaw runtime behavior.
- Focus areas: context window guard, context pruning, compaction safeguard, bootstrap/context injection, memory citations, session/transcript governance.
- Output a closure list: `must align now` / `optional` / `explicitly out-of-scope`.

## Baseline
- AtlasClaw already has the core alignment skeleton in place:
  - Context window guard integrated in runtime.
  - Runtime context pruning (mode/ttl/allow-deny).
  - Compaction safeguard with staged summary and fail-safe.
  - Prompt context resolver with dynamic budget + file cache.
  - Session transcript cache/retry/archive budget.
  - Memory tool structured citations.

## Must Align Now (P0)

### 1) Pruning hard-clear behavior is still coarser than OpenClaw
- Atlas currently hard-clears all prunable tool messages once threshold is crossed, without progressive stop.
- OpenClaw clears progressively and stops once ratio is below threshold.
- Atlas currently only prunes role `tool`, while OpenClaw works on `toolResult`.

Code pointers:
- Atlas: `app/atlasclaw/agent/context_pruning.py:193`, `app/atlasclaw/agent/context_pruning.py:225`, `app/atlasclaw/agent/context_pruning.py:232`
- OpenClaw: `src/agents/pi-extensions/context-pruning/pruner.ts:268`, `src/agents/pi-extensions/context-pruning/pruner.ts:306`, `src/agents/pi-extensions/context-pruning/pruner.ts:318`

Impact:
- Over-pruning risk in long sessions.
- Behavior drift from OpenClaw under high context pressure.

### 2) Compaction path misses OpenClaw’s transcript safety pair-repair and detail stripping
- OpenClaw strips verbose `toolResult.details` before summarization.
- OpenClaw repairs orphan `tool_use`/`tool_result` pairs after chunk drops.
- Atlas currently does staged summarization and history-share pruning, but lacks these two protections.

Code pointers:
- Atlas: `app/atlasclaw/agent/compaction.py:245`, `app/atlasclaw/agent/compaction.py:283`, `app/atlasclaw/agent/compaction.py:425`
- OpenClaw: `src/agents/compaction.ts:7`, `src/agents/compaction.ts:22`, `src/agents/compaction.ts:375`

Impact:
- Higher chance of malformed/less stable context after aggressive history pruning.
- More noise enters summarization when tool details are very large.

### 3) Session governance is still process-local, not production-grade equivalent
- Atlas uses in-process asyncio locks and transcript cache.
- OpenClaw additionally has store-level TTL cache, lock-file watchdog, pruning/capping/rotation, disk budget sweeps.

Code pointers:
- Atlas: `app/atlasclaw/session/manager.py:110`, `app/atlasclaw/session/manager.py:147`, `app/atlasclaw/session/manager.py:195`
- OpenClaw: `src/config/sessions/store.ts:40`, `src/config/sessions/store.ts:219`, `src/config/sessions/store.ts:426`, `src/agents/session-write-lock.ts:1`

Impact:
- Atlas is weaker under multi-process concurrency and large long-lived stores.
- Maintenance control is less complete.

## Important But Can Follow (P1)

### 4) Context window source discovery still simpler than OpenClaw
- OpenClaw merges discovered model metadata and configured model windows (including specific high-window policy cases).
- Atlas mainly resolves from selected token/deps extras/default.

Code pointers:
- Atlas: `app/atlasclaw/agent/runner_execution.py:1142`, `app/atlasclaw/agent/context_window_guard.py:38`
- OpenClaw: `src/agents/context.ts:22`, `src/agents/context.ts:44`, `src/agents/context.ts:172`

### 5) Bootstrap truncation strategy differs
- OpenClaw uses head+tail truncation and budget clamp with minimum remaining budget guard.
- Atlas currently does front truncation.

Code pointers:
- Atlas: `app/atlasclaw/agent/prompt_context_resolver.py:137`, `app/atlasclaw/agent/prompt_context_resolver.py:145`
- OpenClaw: `src/agents/pi-embedded-helpers/bootstrap.ts:114`, `src/agents/pi-embedded-helpers/bootstrap.ts:149`, `src/agents/pi-embedded-helpers/bootstrap.ts:222`

### 6) Memory citation policy still lacks OpenClaw auto/on/off mode semantics
- Atlas always emits citation fields.
- OpenClaw supports `on/off/auto` and suppresses citations by default in group/channel contexts.

Code pointers:
- Atlas: `app/atlasclaw/tools/memory/search_tool.py:65`, `app/atlasclaw/tools/memory/get_tool.py:65`
- OpenClaw: `src/agents/tools/memory-tool.ts:142`, `src/agents/tools/memory-tool.ts:214`, `src/agents/tools/memory-tool.ts:224`

## Explicitly Out Of Scope (Confirmed)
- Bootstrap hook override chain (OpenClaw’s `agent:bootstrap` override path).
  - OpenClaw references: `src/agents/bootstrap-hooks.ts:1`, `src/agents/bootstrap-files.ts:44`
  - This remains intentionally excluded per product decision.

## Recommended Closure Order
1. P0-1 pruning progressive clear + role compatibility.
2. P0-2 compaction safety parity (`strip details` + pair repair).
3. P0-3 session governance parity subset (write lock + maintenance primitives).
4. P1-4 context window discovery merge.
5. P1-5 bootstrap truncation strategy.
6. P1-6 memory citation mode policy.

## Review Conclusion
- AtlasClaw is no longer missing the context-management foundation.
- Remaining deltas are now mostly in runtime robustness and production governance semantics.
- Priority should be P0 first to complete behavioral parity under high-load/long-session scenarios.
