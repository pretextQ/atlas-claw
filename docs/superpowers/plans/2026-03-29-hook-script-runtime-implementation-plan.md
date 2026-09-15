# Hook Script Runtime Implementation Plan

## Scope

This plan is incremental on top of the existing generic Hook Runtime. The generic runtime, event store, sinks, API, and built-in Python handler support already exist. This plan only covers:

1. Aggregated event `run.context_ready`
2. Config-driven local command Script Hook handlers
3. Script stdin/stdout protocol and structured action application
4. Complete regression coverage and a dedicated `docs/HOOK_RUNTIME_GUIDE.md`

## Goals

- Keep the core runtime generic and OpenClaw-aligned
- Preserve existing fine-grained events while adding a consumer-friendly aggregated event
- Allow local executable commands to consume hook events through config
- Accept structured stdout actions and route them into existing pending/memory/context sinks
- Cover the new capabilities with unit, integration, and E2E tests

## Work Breakdown

### 1. Event Model

- Add `HookEventType.RUN_CONTEXT_READY`
- Extend runtime event dispatch helpers with a new aggregator helper
- Emit `run.context_ready` after a run completes and after a run fails
- Payload should include:
  - `user_message`
  - `message_history`
  - `system_prompt`
  - `assistant_message`
  - `tool_calls`
  - `run_status`
  - `error`
  - `session_title`

### 2. Config Schema and Bootstrap

- Add `HookScriptHandlerConfig` and `HooksRuntimeConfig`
- Add `hooks_runtime` to `AtlasClawConfig`
- Load configured script handlers during startup in `main.py`
- Keep script handlers disabled by default unless explicitly configured

### 3. Script Hook Runtime

- Add a script runner module under `app/atlasclaw/hooks/`
- Execute local commands with:
  - event envelope JSON over stdin
  - timeout support
  - optional working directory
  - minimal environment variables:
    - `ATLASCLAW_HOOK_EVENT`
    - `ATLASCLAW_USER_ID`
    - `ATLASCLAW_SESSION_KEY`
    - `ATLASCLAW_RUN_ID`
    - `ATLASCLAW_MODULE`
- Capture stdout/stderr
- Treat non-zero exit and timeout as hook execution failures

### 4. Structured Action Protocol

- Parse stdout JSON with `actions: []`
- Support Phase 1 actions:
  - `create_pending`
  - `write_memory`
  - `add_context`
- Route actions through generic runtime/state/sinks
- Reject malformed or unsupported actions safely

### 5. Tests

- Unit tests:
  - new event type serialization
  - script config parsing
  - script runner success / invalid json / timeout / non-zero exit
  - action application for three supported action types
- Integration tests:
  - runtime dispatch to script handlers
  - `run.context_ready` emission on success and failure
- E2E tests:
  - script handler receives aggregated event
  - script output creates pending items
  - script output writes memory
  - script output adds context

### 6. Documentation

- Add `docs/HOOK_RUNTIME_GUIDE.md`
- Cover:
  - what is already implemented in Hook Runtime
  - event taxonomy
  - `run.context_ready`
  - script hook config
  - stdin/stdout protocol
  - action schema
  - storage paths
  - safety boundaries
  - example command script

## Verification

Run after implementation:

1. `pytest tests/atlasclaw -m "not e2e" -q -p no:cacheprovider`
2. `npm --prefix app/frontend test -- --runInBand`
3. `pytest tests/atlasclaw -m e2e -q -p no:cacheprovider`
