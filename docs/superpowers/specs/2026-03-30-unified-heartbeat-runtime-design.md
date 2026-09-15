# Unified Heartbeat Runtime Design

## 1. Objective

AtlasClaw needs a unified heartbeat framework that covers two distinct but related responsibilities:

1. Agent heartbeat compatible with OpenClaw heartbeat semantics.
2. Channel connection heartbeat for long-lived channel connections such as Feishu, DingTalk, and WeCom.

The framework must not be a narrow feature for one module. It should be a platform runtime that:

- schedules and executes heartbeat jobs,
- persists heartbeat state,
- emits standard heartbeat events,
- integrates with the existing Hook Runtime and session model,
- supports future notification consumers without coupling heartbeat directly to any single output channel.

## 2. Scope

### In Scope

- A unified `HeartbeatRuntime` with typed heartbeat jobs.
- OpenClaw-aligned agent heartbeat semantics including `HEARTBEAT.md`, `every`, `activeHours`, `isolatedSession`, and `HEARTBEAT_OK`.
- Channel long-connection heartbeat monitoring and reconnect policy.
- Standard heartbeat event emission.
- Target resolution that can address user chat, channels, groups, sessions, and threads.
- Per-user persisted heartbeat state under the workspace.
- Runtime observability, failure counters, and degraded state transitions.
- Hooks integration through emitted runtime events rather than direct notification coupling.

### Out of Scope

- A full notification center UI.
- Generic cron infrastructure for unrelated scheduled jobs.
- Replacing SSE/WebSocket transport-level ping/pong already implemented in the API layer.
- Replacing provider-specific SDK reconnect logic inside channel handlers.
- Automatic plugin discovery for heartbeat executors in phase 1.

## 3. Current Baseline

AtlasClaw already has partial heartbeat-related behavior but no unified runtime:

- SSE sends transport heartbeat events from `app/atlasclaw/api/sse.py`.
- WebSocket has ping/pong and timeout handling in `app/atlasclaw/api/websocket.py`.
- `PromptBuilder` can reference `HEARTBEAT.md`, but heartbeat prompt assembly is currently effectively a no-op.
- `ChannelManager` manages connection startup and stop, but does not run a unified ongoing connection heartbeat supervisor.
- Long-connection handlers implement `connect()`, `disconnect()`, and `reconnect()` independently.
- Hook Runtime already exists and can consume events, but heartbeat is not yet a first-class event source.

This design adds a higher-level runtime rather than replacing existing transport heartbeat behavior.

## 4. Design Principles

### 4.1 Unified Runtime, Typed Jobs

Heartbeat should be one runtime with multiple job types, not multiple unrelated schedulers.

### 4.2 OpenClaw-Compatible Semantics, AtlasClaw-Extended Targeting

AtlasClaw should align with OpenClaw heartbeat semantics where they are useful, but extend target routing to fit AtlasClaw's canonical session and channel model.

### 4.3 Events First, Notifications Second

Heartbeat outcomes should first become standard runtime events. Notification, web chat surfacing, or channel delivery should be implemented as consumers of those events.

### 4.4 Self-Healing Before Alerting

Channel heartbeat is primarily a stability mechanism. It should try to recover quietly before escalating.

### 4.5 Low Context Pollution

Agent heartbeat should support isolated execution and quiet success semantics so periodic checks do not pollute primary conversation history unless configured to do so.

## 5. Top-Level Architecture

The recommended architecture is a unified runtime with typed executors.

### 5.1 Core Components

1. `HeartbeatRuntime`
- registers jobs,
- ticks on a fixed cadence,
- schedules due jobs,
- enforces concurrency limits,
- persists state,
- emits heartbeat events.

2. `HeartbeatJob`
- the normalized model for each heartbeat task.

3. `AgentHeartbeatExecutor`
- executes OpenClaw-style heartbeat turns.

4. `ChannelHeartbeatExecutor`
- monitors and repairs long-lived channel connections.

5. `HeartbeatStateStore`
- persists job definitions, state snapshots, and optional local event logs.

6. `HeartbeatEventBridge`
- converts heartbeat outcomes into standard runtime events that can be consumed by Hook Runtime, web chat, or future notification pipelines.

### 5.2 Separation From Existing Systems

- Hook Runtime remains the general event and handler system.
- Heartbeat Runtime remains the periodic scheduling and health execution system.
- Existing SSE and WebSocket transport heartbeat remains in the API transport layer.
- Channel handlers still own provider-specific low-level connect/reconnect logic.

## 6. Job Model

Heartbeat is represented as typed jobs.

### 6.1 Common Job Fields

```json
{
  "job_id": "hb_agent_main_default",
  "job_type": "agent_turn",
  "enabled": true,
  "owner_user_id": "admin",
  "scope": {
    "agent_id": "main",
    "tenant_id": "default"
  },
  "schedule": {
    "kind": "interval",
    "every_seconds": 3600,
    "active_hours": {
      "timezone": "Asia/Shanghai",
      "start": "09:00",
      "end": "22:00"
    }
  },
  "policy": {},
  "target": { "type": "none" },
  "metadata": {}
}
```

### 6.2 Supported Job Types

#### `agent_turn`
Periodic agent-side check or autonomous inspection turn.

#### `channel_connection`
Periodic health and reconnect supervision for a single long-lived channel connection.

## 7. Configuration Model

Configuration must support system defaults plus explicit overrides.

### 7.1 Runtime Configuration

```json
{
  "heartbeat": {
    "enabled": true,
    "runtime": {
      "tick_seconds": 30,
      "max_concurrent_jobs": 16,
      "emit_runtime_events": true,
      "persist_local_event_log": true
    },
    "defaults": {
      "active_hours": {
        "timezone": "Asia/Shanghai",
        "start": "09:00",
        "end": "22:00"
      }
    },
    "agent_turn": {
      "enabled": true,
      "every_seconds": 3600,
      "isolated_session": true,
      "light_context": true,
      "silent_ok": true,
      "heartbeat_file": "HEARTBEAT.md"
    },
    "channel_connection": {
      "enabled": true,
      "check_interval_seconds": 30,
      "failure_threshold": 3,
      "degraded_threshold": 3,
      "reconnect_backoff_seconds": [10, 30, 60, 300]
    }
  }
}
```

### 7.2 Configuration Responsibility

- Normal end users should not be required to manually set `every` or `target`.
- The runtime should derive practical defaults.
- Platform administrators may override defaults when needed.
- AtlasClaw should still support explicit configuration fields that map cleanly to OpenClaw concepts.

## 8. OpenClaw Compatibility

AtlasClaw should align with the following OpenClaw heartbeat semantics.

### 8.1 `every`
Execution interval.

### 8.2 `activeHours`
Time window during which agent heartbeat is allowed to execute.

### 8.3 `isolatedSession`
Whether the heartbeat turn runs in an isolated session context instead of the primary conversational thread.

### 8.4 `HEARTBEAT_OK`
A reserved success marker meaning "nothing actionable found".

If an agent heartbeat returns `HEARTBEAT_OK` and `silent_ok` is enabled, AtlasClaw should treat the job as a successful quiet run and produce only internal events, not user-facing messages.

### 8.5 `target`
AtlasClaw supports and extends the idea of heartbeat targets.

OpenClaw-style target semantics are supported conceptually, but AtlasClaw uses a richer descriptor so targets can address multi-channel and threaded conversation scopes.

## 9. Target Model

Heartbeat targets must support both system-derived defaults and explicit overrides.

### 9.1 Target Descriptor

```json
{
  "type": "thread",
  "user_id": "admin",
  "channel": "feishu",
  "account_id": "conn_001",
  "peer_id": "oc_xxx",
  "thread_id": "thread_123",
  "session_key": "agent:main:..."
}
```

### 9.2 Supported Target Types

- `none`
- `last_active`
- `user_chat`
- `channel_connection`
- `group_chat`
- `session`
- `thread`

### 9.3 Target Semantics

#### `none`
No direct user-facing delivery. Internal events only.

#### `last_active`
Resolve to the last active user-facing conversation target for the job owner.

#### `user_chat`
Send to the user's own web chat or primary direct session.

#### `channel_connection`
Send to a specific channel connection.

#### `group_chat`
Send to a group conversation. For safety, group delivery defaults to summary-only reminders, not full heartbeat transcript replies.

#### `session`
Send to an explicitly resolved session.

#### `thread`
Send to a specific thread inside a scoped session.

### 9.4 Group Delivery Rule

Heartbeat output to group chat must default to summary-only reminders. Full agent-style detailed output should require explicit override.

## 10. Agent Heartbeat Design

### 10.1 Purpose

Agent heartbeat is a periodic autonomous turn that lets AtlasClaw proactively inspect state, summarize issues, or perform quiet checks without waiting for user input.

### 10.2 Typical Uses

- inspect pending work or approvals,
- inspect hook pending queues,
- inspect channel health summaries,
- inspect provider health,
- trigger summary reminders,
- perform periodic memory/session maintenance checks.

### 10.3 Prompt Source

Agent heartbeat should read `HEARTBEAT.md` from the active agent definition path when available.

Prompt assembly should support:
- heartbeat prompt content,
- resolved target info,
- active hours info,
- execution mode,
- light-context hints,
- recent confirmed memory if appropriate.

### 10.4 Context Rules

If `isolated_session` is true:
- heartbeat runs in an isolated session scope,
- it should not pollute the user’s primary conversation thread,
- only explicit selected outcomes may be delivered back to a target.

If `light_context` is true:
- only lightweight state should be injected,
- full conversation history should be avoided.

### 10.5 Result Handling

Agent heartbeat results should be categorized as:
- `ok` -> equivalent to `HEARTBEAT_OK`,
- `summary` -> informative but low urgency,
- `actionable` -> something should be surfaced,
- `failed` -> heartbeat execution failed.

`ok` should be silent by default.

## 11. Channel Connection Heartbeat Design

### 11.1 Purpose

Channel heartbeat exists primarily to keep long-lived channel connections stable.

### 11.2 Supported Channels

This applies to channels whose handlers support long connections, for example:
- Feishu,
- DingTalk,
- WeCom,
- future socket-based providers.

It does not replace API transport heartbeat for browser SSE/WebSocket streaming.

### 11.3 Health Strategy

For each `channel_connection` job, the executor should:
1. check whether the handler is active,
2. run `health_check()` when available,
3. inspect connection status,
4. attempt reconnect if the connection is degraded or disconnected,
5. back off between reconnect attempts,
6. mark the connection degraded after repeated failures,
7. emit events for all meaningful transitions.

### 11.4 Failure Policy

Default behavior:
- first failures: quiet retry,
- repeated failures: degraded state,
- continued failures after threshold: emit alert-worthy events.

The system should prefer self-healing before escalation.

### 11.5 Reconnect Policy

Reconnects should use bounded backoff, for example:
- 10 seconds,
- 30 seconds,
- 60 seconds,
- 300 seconds.

A reconnect attempt should not block unrelated heartbeat jobs.

## 12. State Model

Each heartbeat job needs durable state.

### 12.1 Job Status

Supported status values:
- `idle`
- `scheduled`
- `running`
- `healthy`
- `degraded`
- `failed`
- `paused`

### 12.2 Persisted State Fields

- `status`
- `last_run_at`
- `last_success_at`
- `last_failure_at`
- `next_run_at`
- `consecutive_failures`
- `last_error`
- `last_result_summary`
- `last_target_resolution`
- `last_delivery_result`

## 13. Storage Layout

Heartbeat state must be distinct from Hook state.

### 13.1 Workspace Paths

- `workspace/users/<user_id>/heartbeat/jobs.json`
- `workspace/users/<user_id>/heartbeat/state.json`
- `workspace/users/<user_id>/heartbeat/events.jsonl` (optional local event log)

### 13.2 Why Separate Storage

Heartbeat is a scheduling and health subsystem. Hook Runtime is an event-consumer subsystem. The two should integrate, but not be stored as the same state surface.

## 14. Event Model

Heartbeat must emit standard runtime events first. Notification should be handled by consumers.

### 14.1 Required Event Types

- `heartbeat.agent.started`
- `heartbeat.agent.completed`
- `heartbeat.agent.failed`
- `heartbeat.channel.check_started`
- `heartbeat.channel.check_succeeded`
- `heartbeat.channel.check_failed`
- `heartbeat.channel.reconnect_started`
- `heartbeat.channel.reconnect_succeeded`
- `heartbeat.channel.reconnect_failed`
- `heartbeat.channel.degraded`

### 14.2 Event Envelope

Each event should include:
- `event_type`
- `job_id`
- `job_type`
- `user_id`
- `channel`
- `account_id`
- `session_key`
- `run_id` (when applicable)
- `created_at`
- `payload`

### 14.3 Event-First Escalation Model

Heartbeat should not directly send alert messages as its primary side effect.

Instead:
- heartbeat writes state,
- heartbeat emits events,
- consumers such as Hook Runtime, web chat, admin dashboards, or future notifier services may decide what to do.

This allows web chat and channel endpoints to consume the same heartbeat events without coupling the heartbeat runtime to one output channel.

## 15. Integration With Hook Runtime

Heartbeat should integrate with Hook Runtime by emitting events, not by becoming a Hook source replacement.

### 15.1 Relationship

- HeartbeatRuntime owns periodic scheduling and execution.
- Hook Runtime owns event consumption and extensible downstream actions.

### 15.2 Downstream Uses

Examples:
- web chat can subscribe to heartbeat events,
- channel notification logic can subscribe to degraded events,
- hook scripts can consume heartbeat event envelopes,
- audit trails can retain heartbeat failures or recoveries.

## 16. Delivery Semantics

### 16.1 Internal-Only Runs

For `target.type = none`, the runtime should only persist state and emit events.

### 16.2 Direct User Delivery

For direct user chat targets, the runtime may surface summaries or actionable heartbeat results.

### 16.3 Group Delivery

For group chat targets, only reminder-style summary delivery should be allowed by default.

### 16.4 Delivery as Secondary Concern

Delivery should be an optional consumer step after heartbeat execution, not a required part of every heartbeat job.

## 17. Scheduling Semantics

### 17.1 Runtime Tick

The runtime should wake on a lightweight fixed tick, for example every 30 seconds, and schedule due jobs.

### 17.2 Job Due Resolution

A job is due when:
- enabled,
- within active hours if applicable,
- not already running,
- next run time has passed.

### 17.3 Concurrency Rules

- Jobs should run concurrently up to a bounded limit.
- A single long-running heartbeat must not block the rest of the runtime.
- A single channel reconnect attempt must not block agent heartbeat jobs.

## 18. Observability

The runtime should expose enough information for diagnostics and UI use.

### 18.1 Metrics/Status Needed

- total jobs,
- running jobs,
- healthy jobs,
- degraded jobs,
- failed jobs,
- per-job consecutive failure count,
- recent event summaries.

### 18.2 Admin/Debug Surfaces

A future API surface may expose:
- list jobs,
- list job state,
- list recent heartbeat events,
- pause/resume a job,
- trigger a job manually.

This is not mandatory for phase 1 implementation but the runtime should keep the model compatible with that future surface.

## 19. Error Handling

### 19.1 Agent Heartbeat Failure

- mark the job failed for that run,
- increment failure count,
- emit `heartbeat.agent.failed`,
- do not spam user targets by default.

### 19.2 Channel Heartbeat Failure

- retry quietly,
- update `consecutive_failures`,
- attempt reconnect,
- emit degraded events after threshold,
- do not immediately alert on first failure.

### 19.3 Runtime Isolation

A failing job must not crash the runtime loop or unrelated heartbeat jobs.

## 20. Security and Safety

- Heartbeat jobs must execute under the owning user scope.
- Agent heartbeat must inherit normal session and permission rules.
- Group targets default to summary-only.
- Heartbeat should not bypass role or upstream system permissions.
- Channel reconnect should not expose decrypted secrets outside handler/config boundaries.

## 21. Testing Strategy

### 21.1 Unit Tests

- job due resolution,
- active hour gating,
- backoff calculation,
- `HEARTBEAT_OK` handling,
- target resolution,
- channel degraded threshold transitions,
- event envelope generation.

### 21.2 Integration Tests

- agent heartbeat job executes and emits events,
- channel heartbeat attempts reconnect and updates state,
- Hook Runtime can consume emitted heartbeat events,
- group target enforces summary-only behavior.

### 21.3 End-to-End Tests

- heartbeat runtime starts with configured jobs,
- agent heartbeat completes and records state,
- degraded channel heartbeat emits events,
- event consumers can surface heartbeat results to web chat or other channels.

## 22. Phasing

### Phase 1

- Unified runtime and typed jobs,
- agent heartbeat with OpenClaw-aligned semantics,
- channel heartbeat with quiet retry and degraded events,
- persisted state,
- heartbeat event emission,
- target descriptor support.

### Phase 2

- admin APIs for job inspection and manual triggers,
- richer target inference,
- notification consumers,
- dashboard/health visualization,
- broader provider-specific heartbeat optimizations.

## 23. Recommendation

AtlasClaw should implement heartbeat as a unified runtime with typed jobs.

This gives the platform:
- OpenClaw-compatible autonomous agent heartbeat,
- stronger multi-channel target routing than OpenClaw,
- real channel long-connection stability supervision,
- event-first architecture that composes naturally with Hook Runtime,
- room to grow into richer alerting and automation later.
