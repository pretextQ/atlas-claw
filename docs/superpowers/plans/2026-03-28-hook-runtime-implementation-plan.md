# Hook Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic AtlasClaw Hook Runtime aligned with OpenClaw-style hooks, state storage, and memory/context sinks, then expose generic hook APIs and regression coverage.

**Architecture:** Extend the existing hook system with a generic runtime event model, hook registry, per-user hook state storage, and sink abstractions that integrate with existing memory/session infrastructure. Keep the core runtime skill-agnostic; do not hardcode self-improving semantics.

**Tech Stack:** Python, FastAPI, asyncio, dataclasses, JSONL storage, existing SessionManagerRouter and HistoryMemoryCoordinator, pytest, httpx, Jest unaffected.

---

### Task 1: Add Generic Hook Runtime Models and Store

**Files:**
- Create: `app/atlasclaw/hooks/runtime_models.py`
- Create: `app/atlasclaw/hooks/runtime_store.py`
- Modify: `app/atlasclaw/hooks/system.py`
- Test: `tests/atlasclaw/test_hook_runtime_store.py`

- [ ] Step 1: Write failing store tests for user-isolated hook state, pending lifecycle, and decision logging.
- [ ] Step 2: Run targeted pytest to confirm failure.
- [ ] Step 3: Implement `HookEventType`, `HookEventEnvelope`, `PendingHookItem`, `HookDecisionRecord`, and `HookStateStore` with JSONL persistence under `workspace/users/<user_id>/hooks/<module>/`.
- [ ] Step 4: Extend `HookSystem` to support runtime event registration/dispatch with string-or-enum event keys.
- [ ] Step 5: Re-run targeted pytest until green.

### Task 2: Add Memory and Context Sink Abstractions

**Files:**
- Create: `app/atlasclaw/hooks/sinks.py`
- Modify: `app/atlasclaw/agent/history_memory.py`
- Test: `tests/atlasclaw/test_hook_sinks.py`

- [ ] Step 1: Write failing tests for `MemorySink` writing confirmed hook items into `memory_<timestamp>.md` and for `ContextSink` only surfacing confirmed records.
- [ ] Step 2: Run targeted pytest to confirm failure.
- [ ] Step 3: Implement generic `MemorySink` and `ContextSink` abstractions using existing workspace/user routing and history memory helpers.
- [ ] Step 4: Re-run targeted pytest until green.

### Task 3: Wire Hook Runtime into Runner and Runtime Event Dispatch

**Files:**
- Create: `app/atlasclaw/hooks/runtime.py`
- Modify: `app/atlasclaw/agent/runtime_events.py`
- Modify: `app/atlasclaw/agent/runner.py`
- Modify: `app/atlasclaw/main.py`
- Test: `tests/atlasclaw/test_hook_runtime_integration.py`

- [ ] Step 1: Write failing integration tests proving `run.started`, `run.completed`, `run.failed`, `llm.*`, and `tool.*` events are emitted with a proper envelope.
- [ ] Step 2: Run targeted pytest to confirm failure.
- [ ] Step 3: Implement a generic `HookRuntime` wrapper that owns the registry/store/sinks and emits events from runner/runtime paths without skill-specific semantics.
- [ ] Step 4: Initialize and inject the runtime from `main.py` so API and runner share the same capability layer.
- [ ] Step 5: Re-run targeted pytest until green.

### Task 4: Expose Generic Hook Runtime APIs

**Files:**
- Create: `app/atlasclaw/api/routes_hooks.py`
- Modify: `app/atlasclaw/api/routes.py`
- Modify: `app/atlasclaw/api/schemas.py`
- Modify: `app/atlasclaw/api/deps_context.py`
- Test: `tests/atlasclaw/test_hook_api_routes.py`

- [ ] Step 1: Write failing API tests for `GET /api/hooks/{module}/events`, `GET /api/hooks/{module}/pending`, `POST /api/hooks/{module}/pending/{id}/confirm`, and `POST /api/hooks/{module}/pending/{id}/reject`.
- [ ] Step 2: Run targeted pytest to confirm failure.
- [ ] Step 3: Implement generic routes backed by `HookStateStore`, `MemorySink`, and `ContextSink`.
- [ ] Step 4: Ensure APIContext exposes the runtime safely for authenticated user-scoped access.
- [ ] Step 5: Re-run targeted pytest until green.

### Task 5: Add Generic Built-In Hook Handlers for Runtime Coverage

**Files:**
- Create: `app/atlasclaw/hooks/builtin_handlers.py`
- Modify: `app/atlasclaw/hooks/runtime.py`
- Test: `tests/atlasclaw/test_builtin_hook_handlers.py`

- [ ] Step 1: Write failing tests for a generic built-in handler that records runtime failures into hook state without embedding skill semantics.
- [ ] Step 2: Run targeted pytest to confirm failure.
- [ ] Step 3: Implement minimal built-in handler registration used to prove the runtime works end-to-end.
- [ ] Step 4: Re-run targeted pytest until green.

### Task 6: Documentation and Full Regression

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/module-details.md`
- Modify: `docs/development-spec.md`
- Modify: `tests/atlasclaw/e2e/test_e2e_api.py`

- [ ] Step 1: Update canonical docs to describe Hook Runtime, HookStateStore, MemorySink, ContextSink, and generic hook APIs.
- [ ] Step 2: Expand E2E coverage to hit the new generic hook endpoints and verify at least one runtime-generated hook record is visible through the API.
- [ ] Step 3: Run backend unit tests: `pytest tests/atlasclaw -m "not e2e" -q -p no:cacheprovider`
- [ ] Step 4: Run E2E tests: `pytest tests/atlasclaw -m e2e -q -p no:cacheprovider`
- [ ] Step 5: If needed, restart the local service using the project root `.env` and re-run the E2E flow.
- [ ] Step 6: Perform a final code review pass and summarize any residual risks before completion.
