# Provider-Driven Web Search Design Tracking Plan

## Scope
- Design an enterprise-grade provider-driven web search runtime for AtlasClaw.
- Replace HTML-scrape-first search architecture with a provider registry and selection model.
- Add grounding-provider abstraction, query expansion, official-source prioritization, caching, observability, and integration with Tool Necessity Gate.
- Keep browser automation as an escalation path, not as the primary search runtime.

## Deliverables
1. A complete design spec for Provider-Driven Web Search and Grounding.
2. Updated project state reflecting the new design track and its relationship to Tool Necessity Gate and Full Context Engine.
3. A task plan that maps design workstreams to explicit completion criteria.
4. A document-alignment review confirming `state`, `task`, and `spec` use the same scope, terminology, and next step.

## Design Workstreams

### 1. Baseline and Gap Analysis
Goal: document the current `web_search` implementation and why it is insufficient.

Success criteria:
- [x] Current `web_search` implementation reviewed.
- [x] Current provider behavior documented.
- [x] Gap between HTML-scrape adapters and enterprise provider runtime captured.
- [x] Relationship to Tool Necessity Gate captured.

Key findings:
- Current `web_search` uses `httpx` to fetch Bing/DuckDuckGo/Google HTML result pages and parses them.
- Default provider is Bing, with fallback to DuckDuckGo and Google.
- Search output is not strongly grounded and is not governed by provider capability metadata.
- Query expansion and official-source prioritization are not runtime-first features today.

### 2. Runtime Architecture Decision
Goal: choose the architecture for search and grounding.

Options considered:
- Keep HTML scraping and incrementally patch parsing. Rejected because it is not enterprise-grade.
- Browser-first retrieval. Rejected because it is too heavy for default search.
- Provider-driven runtime with fallback adapters. Recommended.

Success criteria:
- [x] Recommended architecture selected.
- [x] Fallback-only role of HTML-scrape providers documented.
- [x] Integration boundaries with Tool Gate and browser documented.

### 3. Provider Model Design
Goal: define provider classes and runtime registry.

Success criteria:
- [x] Search provider vs grounding provider classes documented.
- [x] Provider capability contract documented.
- [x] Registry responsibilities documented.
- [x] Candidate provider keys listed.

### 4. Result Normalization Design
Goal: define how provider-specific responses become unified runtime results.

Success criteria:
- [x] Normalized search result model documented.
- [x] Grounded search response model documented.
- [x] Unified tool-output expectations documented.

### 5. Provider Selection Strategy
Goal: define how AtlasClaw chooses a provider at runtime.

Success criteria:
- [x] Selection inputs documented.
- [x] Default provider rules documented.
- [x] Grounding preference documented.
- [x] Explicit override behavior documented.

### 6. Query Planning and Expansion
Goal: define structured search recovery instead of one-shot search failure.

Success criteria:
- [x] Query planner responsibilities documented.
- [x] Expansion policy documented.
- [x] Bounded expansion guardrails documented.
- [x] Official-source expansion documented.

### 7. Source Prioritization
Goal: define how official and trusted sources should outrank weak sources.

Success criteria:
- [x] Source tiers documented.
- [x] Priority rules documented.
- [x] Enterprise trust policy direction documented.

### 8. Tool Gate and Browser Integration
Goal: integrate the new search runtime with current runtime policy layers.

Success criteria:
- [x] Mandatory Tool Enforcement relationship documented.
- [x] Anti-fabrication rule documented.
- [x] Browser escalation criteria documented.
- [x] Browser explicitly documented as non-default search path.

### 9. Caching, Events, and Observability
Goal: make search runtime enterprise-grade and debuggable.

Success criteria:
- [x] Cache strategy documented.
- [x] Event taxonomy documented.
- [x] Metrics documented.
- [x] Hook Runtime integration direction documented.

### 10. Testing and Rollout Strategy
Goal: make the design implementation-ready.

Success criteria:
- [x] Unit test scope listed.
- [x] Integration test scope listed.
- [x] E2E scenarios listed.
- [x] Rollout phases documented.

## Verification
- command: review the current search implementation, current Tool Necessity Gate spec, and project state; then self-review the new provider-driven search spec for placeholders, contradictions, staging errors, and terminology drift
- expected: one implementation-ready design spec plus aligned state/task docs describing the same search-runtime enhancement track
- actual: spec written at `docs/superpowers/specs/2026-03-31-provider-driven-web-search-design.md`; state/task/spec aligned on provider-driven search, grounding abstraction, query expansion, official-source prioritization, and Tool Gate integration

## Handoff Notes
- This design intentionally strengthens AtlasClaw beyond the current HTML-based `web_search` implementation.
- The design aligns with OpenClaw's provider-driven direction while adding stronger enterprise source governance and runtime integration.
- The design-review stage is complete and an implementation plan now exists at `docs/superpowers/plans/2026-03-31-provider-driven-web-search-implementation-plan.md`.
- The next step is to choose an execution mode and implement the plan task-by-task.
