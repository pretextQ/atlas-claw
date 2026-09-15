# Provider/Skill Metadata Hint Layer Design (No Hardcoded Keywords)

## 1. Goal

Build a generic metadata-driven hint layer so Provider/Skill metadata can guide tool selection without introducing hardcoded business keyword routing.

This design must satisfy:

1. No hardcoded domain keyword branch in runtime flow.
2. One unified tool loop for built-in/provider/skill tools.
3. Metadata acts as soft ranking/context signals, not hard gate.
4. Architecture remains provider-agnostic (CMP, ServiceNow, Datadog, CyberArk, future providers).

## 2. Current State and Gap

### 2.1 What already works

1. Provider frontmatter (`keywords/capabilities/use_when/avoid_when`) is parsed into provider context.
2. Skill frontmatter metadata is retained in markdown snapshot.
3. Runtime has minimal-toolset filtering and unified tool list construction.

### 2.2 Main gap

1. Provider metadata currently has limited runtime impact (mostly fallback hint usage).
2. Skill metadata fields like `triggers/use_when/avoid_when` are mostly passive prompt/index data.
3. There is no explicit, auditable “metadata -> candidate ranking” stage before model loop.

## 3. Design Principles

1. LLM decides tool calls; runtime does not hardcode business intent.
2. Policy first, ranking second:
   - Policy narrows allowed set.
   - Metadata ranks within the allowed set.
3. Ranking is soft:
   - order/priority guidance only,
   - no forced tool call based on metadata text matching alone.
4. Same mechanism for all providers and skills.

## 4. Unified Metadata Runtime Model

## 4.1 Input sources

1. Provider metadata from `PROVIDER.md`:
   - `provider_type`, `display_name`, `version`,
   - `keywords`, `capabilities`, `use_when`, `avoid_when`.
2. Skill metadata from `SKILL.md`:
   - `name`, `description`, `provider_type`, `triggers`, `use_when`, `avoid_when`,
   - tool declarations (`tool_<id>_*`).
3. Normalized runtime tool records:
   - `name`, `description`, `provider_type`, `group_ids`, `capability_class`, `priority`.

## 4.2 New normalized hint document

Each turn builds in-memory hint docs:

```json
{
  "hint_id": "provider:smartcmp",
  "hint_type": "provider",
  "provider_type": "smartcmp",
  "tool_names": ["smartcmp_list_pending", "smartcmp_get_request_detail"],
  "group_ids": ["group:smartcmp", "group:cmp"],
  "capability_classes": ["provider:smartcmp"],
  "hint_text": "SmartCMP ... keywords/capabilities/use_when/avoid_when ...",
  "priority": 100
}
```

Skill-level hint docs follow the same shape (`hint_type = skill`) and include `triggers/use_when/avoid_when`.

## 5. Turn-Time Flow

## 5.1 Pipeline

1. Build minimal executable toolset from policy pipeline.
2. Build provider/skill hint docs from metadata (only for tools still allowed).
3. Run lightweight metadata ranker (LLM classifier) to produce preferred candidates.
4. Reorder allowed tool list by rank result + declared priority.
5. Enter main model loop with reordered allowed tools.
6. Main model decides tool calls and whether to continue loop.

No web-only special branch is introduced here.

## 5.2 Metadata ranker contract

Ranker input:

1. user message + short recent context
2. allowed tool metadata
3. provider/skill hint docs

Ranker output JSON:

```json
{
  "preferred_provider_types": ["smartcmp"],
  "preferred_capability_classes": ["provider:smartcmp"],
  "preferred_tool_names": ["smartcmp_list_pending"],
  "confidence": 0.82,
  "reason": "User asks CMP pending approvals"
}
```

Ranker constraints:

1. Must not output tool names outside allowed set.
2. Output is ranking hint only, not execution command.
3. Timeout fallback: keep existing allowed order by runtime priority.

## 6. How Metadata Fields Take Effect

## 6.1 Provider metadata effect

`keywords/capabilities/use_when/avoid_when` are used to build provider hint text for ranking context.

Runtime effect:

1. Improve provider candidate ordering.
2. Reduce wrong-provider drift when multiple provider tools coexist.
3. Provide auditable reason text for ranking decisions.

## 6.2 Skill metadata effect

`triggers/use_when/avoid_when` become skill hint text bound to skill-declared tools.

Runtime effect:

1. Improve intra-provider tool ordering.
2. Improve selection among multiple skills under same provider.
3. Preserve generic behavior across providers (no fixed keyword list in code).

## 7. Scoring and Ordering (No Hardcoded Business Rules)

Ordering score is computed from:

1. ranker preference buckets:
   - provider match boost,
   - capability class match boost,
   - explicit tool name match boost.
2. tool priority fallback.
3. deterministic stable tie-break by tool name.

No business-domain hardcoded token list is allowed in runtime routing logic.

## 8. Prompt and Context Behavior

1. Skills section remains compact index (`name|description|file_path`).
2. Runtime may append a compact “Top Tool Hints” section from ranker result (bounded tokens), for example:
   - preferred provider,
   - top 3 candidate tools with one-line reason.
3. Full provider/skill metadata is not blindly injected; only bounded summary is injected.

## 9. Failure and Degradation Rules

1. If ranker fails/timeouts:
   - keep policy-filtered tools unchanged,
   - continue normal loop (no hard fail).
2. If metadata missing:
   - fallback to tool normalized fields (`name/description/capability_class/priority`).
3. If provider metadata conflicts with skill metadata:
   - tool-level metadata wins for that tool,
   - provider-level metadata remains coarse hint only.

## 10. Observability and Auditability

Emit runtime events:

1. `hint_ranking_started`
2. `hint_ranking_completed`
3. `hint_ranking_fallback`

Event payload includes:

1. top providers/capabilities/tools
2. confidence
3. reason
4. elapsed milliseconds

Metrics:

1. ranker latency p50/p95
2. tool-call hit rate for top-ranked tool
3. wrong-provider call rate

## 11. Data Model and API Additions

## 11.1 Runtime payload extension (internal)

`deps.extra` additions:

1. `provider_hint_docs`
2. `skill_hint_docs`
3. `tool_ranking_trace`

## 11.2 Optional API visibility

For diagnostics, `/api/skills` can optionally expose normalized metadata fields:

1. `provider_type`
2. `group_ids`
3. `capability_class`
4. `priority`

Default response may keep compact mode for UI compatibility.

## 12. Rollout Plan

1. Phase A: metadata normalization and hint-doc builder.
2. Phase B: lightweight ranker and ordering integration.
3. Phase C: runtime events/metrics and debug trace.
4. Phase D: E2E for multi-provider prompts (CMP, ServiceNow, Datadog, CyberArk scenarios).

## 13. Acceptance Criteria

1. Provider metadata and skill metadata both have measurable runtime effect on candidate ordering.
2. No new hardcoded domain keyword routing is introduced.
3. All tools remain in one unified loop.
4. With multiple providers enabled, wrong-provider rate decreases in E2E regression set.
5. Ranking decisions are traceable in runtime events.

## 14. Out of Scope

1. Provider-specific prompt hard templates.
2. Business-specific if-else routing in runner.
3. Replacing model loop with deterministic workflow engine.

