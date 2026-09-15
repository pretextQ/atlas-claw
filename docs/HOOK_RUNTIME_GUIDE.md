# Hook Runtime Guide

## Overview

AtlasClaw provides a generic Hook Runtime aligned with OpenClaw-style runtime hooks. The Hook Runtime is separate from the legacy phase-based `HookSystem`.

- `HookSystem`: phase-oriented in-process interception for core execution phases
- `HookRuntime`: typed runtime events, per-user module state, pending decisions, memory/context sinks, and config-driven script handlers

The runtime stays generic by design. It does not encode skill-specific semantics. Skills or external modules consume hook events as clients of the runtime.

## What Is Implemented

### Core Runtime

Implemented modules:

- `app/atlasclaw/hooks/runtime_models.py`
- `app/atlasclaw/hooks/runtime_store.py`
- `app/atlasclaw/hooks/runtime_sinks.py`
- `app/atlasclaw/hooks/runtime.py`
- `app/atlasclaw/hooks/runtime_builtin.py`
- `app/atlasclaw/api/routes_hooks.py`

Core capabilities:

- typed runtime event envelopes
- in-process Python hook handlers
- config-driven local command Script Hook handlers
- per-user, per-module event and pending state
- memory promotion sink
- context injection sink
- user confirmation / rejection workflow
- hook API routes for events and pending decisions

### Event Taxonomy

Supported event types:

- `run.started`
- `run.completed`
- `run.failed`
- `run.context_ready`
- `message.received`
- `message.user_corrected`
- `llm.requested`
- `llm.completed`
- `llm.failed`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `memory.confirmed`
- `memory.rejected`

### Aggregated Context Event

`run.context_ready` is the consumer-friendly aggregated event.

It is emitted after a run completes and after a run fails. Its payload contains the high-value context a hook consumer usually needs without forcing the consumer to stitch together multiple lifecycle events.

Payload fields:

- `user_message`
- `message_history`
- `system_prompt`
- `assistant_message`
- `tool_calls`
- `run_status`
- `error`
- `session_title`

### Script Hook Support

Script hooks are registered from configuration under `hooks_runtime.script_handlers`.

Each handler subscribes to one or more runtime events and executes a local command.

Supported configuration fields:

- `module`: stable module name
- `events`: subscribed runtime events
- `command`: local executable command array
- `timeout_seconds`: per-execution timeout
- `enabled`: explicit enable flag
- `cwd`: optional working directory
- `priority`: execution priority

Example:

```json
{
  "hooks_runtime": {
    "script_handlers": [
      {
        "module": "runtime-audit-script",
        "events": ["run.context_ready", "run.failed"],
        "command": ["python", "scripts/hook_consumer.py"],
        "timeout_seconds": 10,
        "enabled": true,
        "cwd": ".",
        "priority": 100
      }
    ]
  }
}
```

## Storage Layout

Hook state is stored per user and per module:

- `workspace/users/<user_id>/hooks/<module_name>/events.jsonl`
- `workspace/users/<user_id>/hooks/<module_name>/pending.jsonl`
- `workspace/users/<user_id>/hooks/<module_name>/decisions.jsonl`
- `workspace/users/<user_id>/hooks/<module_name>/context.jsonl`

Long-term memory files remain separate:

- `workspace/users/<user_id>/memory/memory_<timestamp>.md`

## Event Envelope

Every runtime event is represented by `HookEventEnvelope`.

Envelope fields:

- `id`
- `event_type`
- `user_id`
- `session_key`
- `run_id`
- `channel`
- `agent_id`
- `created_at`
- `payload`

Script hooks receive the full envelope as JSON over `stdin`.

## Script Hook Protocol

### Input

The runtime writes the full event envelope JSON to the script process through `stdin`.

Additional environment variables:

- `ATLASCLAW_HOOK_EVENT`
- `ATLASCLAW_USER_ID`
- `ATLASCLAW_SESSION_KEY`
- `ATLASCLAW_RUN_ID`
- `ATLASCLAW_MODULE`

Not exposed by default:

- user tokens
- provider API keys
- full dependency objects
- database connection objects

### Output

The script writes JSON to `stdout`.

Top-level schema:

```json
{
  "actions": [
    {
      "type": "create_pending",
      "summary": "Review this runtime event",
      "body": "Assistant misunderstood approval scope",
      "metadata": {"source": "script"}
    },
    {
      "type": "write_memory",
      "title": "Confirmed lesson",
      "body": "Always ask for approver scope before finalizing",
      "metadata": {"source": "script"}
    },
    {
      "type": "add_context",
      "summary": "Recent preference",
      "body": "User prefers concise remediation steps",
      "metadata": {"source": "script"}
    }
  ]
}
```

If the script prints nothing, the runtime treats it as no-op.

If the script returns invalid JSON, times out, or exits non-zero, the runtime logs the failure and does not fail the main agent run.

## Structured Actions

### `create_pending`

Purpose:

- create a generic pending item for later user confirmation

Required fields:

- `summary`

Optional fields:

- `body`
- `metadata`

Effect:

- appends a pending record into `pending.jsonl`

### `write_memory`

Purpose:

- write directly into AtlasClaw long-term memory

Required fields:

- `body`

Optional fields:

- `title`
- `metadata`

Effect:

- writes a `memory_<timestamp>.md` file under the user memory directory
- emits `memory.confirmed`

### `add_context`

Purpose:

- add a confirmed context injection record for future recall

Required fields:

- `summary`

Optional fields:

- `body`
- `metadata`

Effect:

- appends a generic context record into `context.jsonl`
- makes the item visible through `ContextSink.list_confirmed(...)`

## Python Handlers vs Script Handlers

### Python handler

Use when:

- the behavior is internal to AtlasClaw
- the hook needs direct Python access to runtime sinks/store
- low-latency in-process execution matters

Registration path:

- `HookRuntime.register(HookHandlerDefinition(...))`

### Script handler

Use when:

- an external module or skill wants to consume hook events
- you want an OpenClaw-style local command hook experience
- the consumer should stay outside AtlasClaw core code

Registration path:

- `hooks_runtime.script_handlers` in config

## API Endpoints

Generic hook runtime API:

- `GET /api/hooks/{module}/events`
- `GET /api/hooks/{module}/pending`
- `POST /api/hooks/{module}/pending/{id}/confirm`
- `POST /api/hooks/{module}/pending/{id}/reject`

The runtime API is module-scoped and user-scoped.

## Example Script

```python
import json
import sys


def main() -> None:
    event = json.load(sys.stdin)
    payload = event.get("payload", {})
    if event.get("event_type") != "run.context_ready":
        print(json.dumps({"actions": []}))
        return

    user_message = str(payload.get("user_message") or "").strip()
    assistant_message = str(payload.get("assistant_message") or "").strip()

    actions = []
    if user_message and assistant_message:
        actions.append(
            {
                "type": "create_pending",
                "summary": f"Review conversation: {user_message[:40]}",
                "body": assistant_message,
                "metadata": {"source": "example-script"},
            }
        )

    print(json.dumps({"actions": actions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

## Safety Boundaries

Phase 1 safety rules:

- script handlers are disabled unless explicitly configured
- only local executable commands are supported
- every script execution has a timeout
- only a minimal environment is injected
- no secret provider tokens are exposed by default
- only three generic structured actions are supported
- script failures do not take down the primary agent run

## Recommended Usage Patterns

### Observability / Audit

Subscribe to:

- `run.failed`
- `tool.failed`
- `llm.failed`
- `run.context_ready`

Use actions:

- `create_pending`
- optional `write_memory`

### Learning / Self-improving Skills

Do not hard-code learning semantics into the core.

Instead:

- consume `run.context_ready`, `message.user_corrected`, `run.failed`
- produce generic actions through script or Python handlers
- rely on the generic pending/memory/context sinks

### Context Enrichment

Use `add_context` only for concise, durable context that is safe to surface later.

Avoid dumping whole transcripts into context storage.

## Test Coverage

Current coverage includes:

- runtime store behavior
- sinks
- runtime dispatch
- runner integration
- hook API routes
- script hook runner and structured action application
- E2E coverage for pending, memory, context, and script hook execution

## Related Files

- `app/atlasclaw/hooks/runtime_models.py`
- `app/atlasclaw/hooks/runtime_store.py`
- `app/atlasclaw/hooks/runtime_sinks.py`
- `app/atlasclaw/hooks/runtime.py`
- `app/atlasclaw/hooks/runtime_script.py`
- `app/atlasclaw/hooks/runtime_builtin.py`
- `app/atlasclaw/api/routes_hooks.py`
- `app/atlasclaw/agent/runtime_events.py`
- `app/atlasclaw/agent/runner.py`
