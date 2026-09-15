# Tool/Skill/Provider Minimal Toolset Alignment Design

## 1. Goal

Align AtlasClaw tool and skill runtime with the OpenClaw-style model:

- Skills are injected into prompt as a compact index only.
- LLM loads skill details on demand via `read` for target `SKILL.md`.
- Tool execution stays in one unified loop (no special web-only branch).
- Provider tools and provider-defined groups are first-class runtime citizens.
- Each turn computes a minimal executable toolset before model loop.

This design must preserve AtlasClaw provider workflow quality (for example SmartCMP).

## 2. Scope

### In scope

1. Prompt skill section redesign:
   - compact list only: `name`, `description`, `file_path`
   - explicit instruction to use `read` on demand for full `SKILL.md`
2. Built-in tool catalog expansion and group normalization.
3. Provider tool/group metadata ingestion and runtime merge.
4. Runtime minimal-toolset policy pipeline (OpenClaw-style monotonic narrowing).
5. Runner integration so model loop only sees filtered tools.
6. E2E verification with real agent path and timing report.

### Out of scope

1. New crawler/search ranking redesign.
2. New business-domain heuristics.
3. Full policy UI management panel.

## 3. Current Gaps (confirmed from code)

1. `MD Skills` prompt currently injects rich XML metadata instead of compact index.
2. Built-in tool registration currently excludes existing filesystem/runtime tools (`read/write/edit/exec/process`).
3. Group map is static and limited; provider-defined groups (for example `group:cmp`) are not supported.
4. `tools_snapshot` is global/full snapshot, not per-turn minimal filtered set.
5. Policy filtering is not currently applied as a stable pre-loop pipeline.

## 4. Target Architecture

```text
Tool sources
  = built-in tools
  + provider skill tools
  + standalone skill tools

-> metadata normalization
-> group registry merge (built-in + provider-defined)
-> policy pipeline (allow/deny layers, monotonic narrowing)
-> minimal executable toolset for this turn
-> one unified LLM loop (model decides whether/which tools to call)
```

## 5. Prompt Model (Skills Section)

### 5.1 Required output format

The `Skills` section must contain compact entries:

- `name`
- `description` (short)
- `file_path`

No detailed trigger/use_when/examples block in prompt.

### 5.2 Required instruction text

Prompt must explicitly instruct:

1. If a task matches a skill, use `read` on that `SKILL.md` path to load details.
2. Do not assume full skill details are already present.
3. Prefer available executable tools for execution after reading guidance.

## 6. Tool Metadata Contract

Each tool passed to runtime filtering and model must be normalized with:

- `name`
- `description`
- `source` (`builtin` | `provider` | `md_skill`)
- `provider_type` (optional)
- `group_ids` (list)
- `capability_class` (optional)
- `priority` (optional)

## 7. Group Model

### 7.1 Built-in groups

Must include at least:

- `group:runtime` -> `exec`, `process`
- `group:fs` -> `read`, `write`, `edit`, `apply_patch` (if registered)
- `group:web` -> `web_search`, `web_fetch`, `x_search` (if registered)
- `group:ui` -> `browser`, `canvas` (if registered)
- `group:automation` -> `cron`, `gateway` (if registered)
- `group:atlasclaw` -> all built-in core tools

`(if registered)` means group membership is defined but only existing tools are expanded.

### 7.2 Provider-defined groups

Providers may define extra groups, for example:

- `group:cmp`

Group definitions are merged into runtime group registry and available to policy expansion.

## 8. Policy Pipeline (Minimal Toolset)

### 8.1 Layers

Policy layers are processed in fixed order:

1. base profile layer
2. global layer
3. provider layer
4. agent layer
5. channel/session layer

### 8.2 Per-layer rule

Each layer applies same logic:

1. expand aliases/globs/groups
2. apply deny first
3. if allow is empty -> do not narrow in this layer
4. if allow is non-empty -> intersect
5. output goes to next layer

Set is monotonic (cannot grow again after filtering).

## 9. Runner Integration Rules

1. Build filtered toolset before model loop starts.
2. Only filtered toolset is visible to model for this turn.
3. Keep one unified loop for all tools (provider/web/session/etc).
4. Do not create special controlled-web execution branch.

## 10. Provider/Skill Accuracy Expectation

For prompts such as:

- "查看 CMP 的待审批"

Runtime should improve tool-call precision by:

1. reducing tool candidates to minimal relevant set,
2. preserving provider group/class metadata,
3. allowing model to select CMP tools from a narrower candidate space.

## 11. Acceptance Criteria

1. Prompt `Skills` section is compact-index format and includes read-on-demand instruction.
2. Built-in groups listed in section 7 are supported by catalog expansion.
3. Provider-defined groups are loadable and resolvable by `group:*`.
4. Pre-loop minimal toolset filtering is active and test-covered.
5. Unified loop behavior remains (no web special branch).
6. Real-agent E2E passes for provider scenario:
   - user turn 1: list CMP pending approvals
   - user turn 2: get detail for one ticket
   - user turn 3: list CMP service catalogs
7. E2E report includes per-turn runtime elapsed timings and response payload summary.

## 12. Risks and Guardrails

1. Over-filtering can hide required tools.
   - Guardrail: if filtered set is empty, emit warning and fall back to safe minimal default profile.
2. Provider metadata inconsistency.
   - Guardrail: strict metadata normalization + fallback defaults.
3. Prompt regressions from section shape change.
   - Guardrail: unit tests for prompt rendering and instruction presence.

## 13. Rollout Plan

1. Phase A: prompt compact skill index + read guidance.
2. Phase B: tool metadata and group registry refactor.
3. Phase C: policy pipeline and runner minimal-set integration.
4. Phase D: tests + real-agent E2E + docs/state/task reconciliation.

## 14. Delivery Constraint

No "minimal implementation" is allowed for this change set.

1. Every scoped feature must be implemented end-to-end (runtime + tests + docs where applicable).
2. Temporary stubs, placeholder branches, and intentionally incomplete fallback paths are not acceptable.
3. Each task is considered complete only after two reviews:
   - Review A: spec alignment
   - Review B: code and verification completeness

## 15. Unified Metadata Contract (Normative)

This spec adopts `docs/UNIFIED_PROVIDER_TOOL_CONTRACT.md` as the normative contract.

### 15.1 Provider metadata in `PROVIDER.md`

Required:

- `provider_type`
- `display_name`
- `version`

Recommended:

- `keywords`
- `capabilities`
- `use_when`
- `avoid_when`

### 15.2 Skill metadata in `SKILL.md`

Required:

- `name`
- `description`

Provider-skill recommended:

- `provider_type`
- `instance_required`
- `use_when`
- `avoid_when`
- `triggers`

Executable tool declaration:

- canonical pair: `tool_<id>_name` + `tool_<id>_entrypoint`
- optional: `tool_<id>_group`, `tool_<id>_groups`, `tool_<id>_capability_class`, `tool_<id>_priority`

### 15.3 Standard enterprise examples

The following standard examples are defined in the contract document:

- SmartCMP
- ServiceNow
- Datadog
- CyberArk

