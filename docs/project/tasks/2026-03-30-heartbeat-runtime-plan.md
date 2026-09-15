# Unified Heartbeat Runtime Design Tracking Plan

## Scope
- Design a unified heartbeat runtime for AtlasClaw.
- Cover both OpenClaw-aligned agent heartbeat semantics and AtlasClaw-specific channel long-connection heartbeat supervision.
- Keep heartbeat runtime separate from Hook scheduling, but integrate through emitted runtime events.
- Produce a spec that is implementation-ready and scoped for a subsequent implementation plan.

## Deliverables
1. A complete design spec covering architecture, config model, job model, state model, target model, events, failure handling, storage, and testing strategy.
2. Updated project state file with current baseline, implementation progress, risks, and next steps.
3. A design-tracking task file that maps major design decisions to explicit completion criteria.
4. Completed implementation and verification aligned to the approved heartbeat spec.

## Design Workstreams

### 1. Baseline and Compatibility Analysis
Goal: document what already exists and what must be added.

Success criteria:
- [x] Existing SSE/WebSocket transport heartbeat behavior reviewed.
- [x] Existing channel connection lifecycle reviewed.
- [x] Existing Hook Runtime reviewed.
- [x] OpenClaw heartbeat semantics reviewed.
- [x] Baseline and gap summary captured in the spec.

Key findings:
- SSE and WebSocket already have transport heartbeat behavior.
- Channel handlers support `connect/disconnect/reconnect`, but there is no unified connection-heartbeat supervisor.
- PromptBuilder references `HEARTBEAT.md`, but agent heartbeat is not implemented as a runtime feature.
- Hook Runtime already exists and should consume heartbeat events, not be overloaded with scheduling.

### 2. Runtime Architecture Decision
Goal: choose the top-level heartbeat architecture.

Options considered:
- Split executors with minimal shared state.
- Unified runtime with typed jobs. Recommended.
- Hook-driven scheduling. Rejected for phase 1.

Success criteria:
- [x] One recommended architecture selected.
- [x] Rejected alternatives and reasons captured.
- [x] Clear separation from Hook Runtime documented.

Chosen direction:
- Unified `HeartbeatRuntime` with typed jobs.
- Executors specialized by job type.
- Shared runtime scheduling, state persistence, concurrency control, and event emission.

### 3. Agent Heartbeat Design
Goal: define OpenClaw-aligned agent heartbeat behavior.

Success criteria:
- [x] OpenClaw compatibility semantics documented:
  - `every`
  - `activeHours`
  - `isolatedSession`
  - `HEARTBEAT_OK`
  - `target`
- [x] AtlasClaw-specific rules for quiet success and low-context execution documented.
- [x] `HEARTBEAT.md` integration documented.
- [x] Result categories documented.

Chosen rules:
- Agent heartbeat is a periodic autonomous turn.
- `HEARTBEAT_OK` is treated as silent success by default.
- `isolatedSession` avoids polluting the primary user thread.
- `light_context` reduces token and history cost.

### 4. Channel Connection Heartbeat Design
Goal: define connection stability supervision for long-lived channels.

Success criteria:
- [x] Scope limited to long-lived channel connections.
- [x] Quiet retry and reconnect strategy defined.
- [x] Degraded/failure thresholds defined.
- [x] Relationship to provider-specific reconnect logic clarified.

Chosen rules:
- Channel heartbeat exists primarily to keep long connections stable.
- First failures are handled quietly.
- Repeated failures move the job into degraded state.
- Alert-worthy behavior begins only after repeated failure thresholds.

### 5. Target Model Design
Goal: extend OpenClaw-style target semantics to AtlasClaw session/channel scope.

Success criteria:
- [x] Target descriptor structure defined.
- [x] Supported target types listed.
- [x] Group-chat safety rule documented.
- [x] Resolution model aligned with AtlasClaw session/channel scope.

Supported target types:
- `none`
- `last_active`
- `user_chat`
- `channel_connection`
- `group_chat`
- `session`
- `thread`

Chosen rules:
- Group targets default to summary-only reminders.
- Targets may be system-derived by default and overridden by administrators when needed.

### 6. State and Storage Model
Goal: define durable heartbeat state.

Success criteria:
- [x] Typed job model documented.
- [x] Persistent state fields documented.
- [x] Workspace storage paths defined.
- [x] Separation from Hook state justified.

Storage paths:
- `workspace/users/<user_id>/heartbeat/jobs.json`
- `workspace/users/<user_id>/heartbeat/state.json`
- `workspace/users/<user_id>/heartbeat/events.jsonl`

### 7. Event Model and Hook Integration
Goal: make heartbeat an event source without making it a hook scheduler.

Success criteria:
- [x] Heartbeat event taxonomy defined.
- [x] Event envelope shape documented.
- [x] Event-first escalation model documented.
- [x] Hook Runtime relationship documented.

Chosen rules:
- Heartbeat emits standard events first.
- Web chat, channels, hook consumers, dashboards, and notifiers are downstream consumers.
- Heartbeat is not directly coupled to a notification channel.

### 8. Failure Handling and Observability
Goal: define runtime behavior under failure and the minimum monitoring surface.

Success criteria:
- [x] Agent failure handling defined.
- [x] Channel failure handling defined.
- [x] Runtime isolation guarantees defined.
- [x] Metrics/status expectations listed.

### 9. Testing Strategy
Goal: ensure the design is specific enough to support a later implementation plan.

Success criteria:
- [x] Unit test areas listed.
- [x] Integration test areas listed.
- [x] End-to-end test areas listed.
- [x] Testing scope aligned with phase 1 deliverables.

## Verification
- command: review docs and implementation baseline, then inspect the completed spec for placeholders, contradictions, and missing boundaries
- expected: a complete spec with explicit decisions, compatibility boundaries, and phase scope
- actual: completed spec written at `docs/superpowers/specs/2026-03-30-unified-heartbeat-runtime-design.md`; plan expanded to mirror the spec workstreams and decisions

## Implementation Status

- [x] Heartbeat config schema and runtime models implemented
- [x] Heartbeat state store and target resolution implemented
- [x] Agent heartbeat executor implemented
- [x] Channel heartbeat executor implemented
- [x] Unified runtime scheduling, state persistence, and local event logging implemented
- [x] Startup wiring and channel job refresh loop implemented
- [x] Startup registration now derives agent heartbeat jobs from existing isolated user sources instead of the default user bucket
- [x] Startup channel auto-start now iterates real isolated users instead of the default user bucket
- [x] Heartbeat event bridge into Hook Runtime implemented
- [x] Unit and focused E2E coverage implemented
- [x] Canonical docs updated to reflect heartbeat runtime
- [x] Full live-server E2E rerun and final post-implementation review

## Handoff Notes
- The heartbeat workstream is implemented and verified against the approved spec.
- The startup path now follows existing user isolation for both agent heartbeat jobs and enabled channel connection bootstrap.
- Remaining action is user review and approval for any local commit/push step.
