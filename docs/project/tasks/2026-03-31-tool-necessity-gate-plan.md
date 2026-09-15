# Tool Necessity Gate Design Tracking Plan

## Scope
- Design the Phase 1 runtime policy layer that determines when AtlasClaw must use tools or external systems before producing a reliable answer.
- Cover three cooperating layers:
  - Tool Necessity Gate
  - Capability Matcher
  - Mandatory Tool Enforcement
- Include only the minimal context-integration rules required for Phase 1.
- Keep the design compatible with the current runner, prompt builder, Hook Runtime, tools, sessions, and memory systems.
- Produce a spec that is implementation-ready and explicitly staged ahead of the separate Full Context Engine design.

## Deliverables
1. A complete Phase 1 design spec covering classifier inputs, decision outputs, capability matching, enforcement policies, minimal context integration, prompt/runtime integration, observability, and testing.
2. A companion Full Context Engine design spec that scopes the later, larger context orchestration system.
3. Updated project state file with current baseline, risks, and next step.
4. A design-tracking task file that maps the major design decisions to explicit completion criteria.
5. A document alignment review confirming `state`, `task`, and both `spec` files describe the same staged scope and next step.

## Design Workstreams

### 1. Baseline and Gap Analysis
Goal: document what exists today and why it is insufficient.

Success criteria:
- [x] Current prompt and runtime flow reviewed.
- [x] Current tool registration path reviewed.
- [x] Existing tools and external capability surfaces listed.
- [x] Gap between "tool availability" and "tool-required enforcement" captured.
- [x] Gap between existing context assembly and a full Context Engine captured.

Key findings:
- Current time is injected into the prompt, but time-sensitive and externally-dependent questions are not required to use tools.
- AtlasClaw already has `web_search`, `web_fetch`, browser automation, provider tools, Hook Runtime, memory, and session context.
- The current runtime lets the model decide whether to use tools, which can lead to confident but ungrounded answers.
- AtlasClaw already has basic context assembly, but not an explicit Context Engine that governs source selection, ranking, transformation, budgeting, and provenance.

### 2. Runtime Architecture Decision
Goal: choose the policy architecture that sits between user request understanding and tool execution.

Options considered:
- Prompt-only guidance. Rejected because it still depends too much on model self-discipline.
- Rule-only router. Rejected because it will not generalize well across problem types.
- Three-layer policy runtime plus staged Context Engine. Recommended.

Success criteria:
- [x] One recommended architecture selected.
- [x] Rejected alternatives and reasons documented.
- [x] Clear integration boundaries with the current runner and tool stack documented.
- [x] Phase split between runtime policy and full Context Engine documented.

Chosen direction:
- Phase 1 runtime policy pipeline:
  - Tool Necessity Gate
  - Capability Matcher
  - Mandatory Tool Enforcement
  - Minimal Context Integration
- Phase 2 full Context Engine:
  - Source Registry
  - Selection
  - Ranking
  - Transformation
  - Budgeting
  - Provenance

### 3. Tool Necessity Gate Design
Goal: define how the system classifies whether a question can be answered directly or requires tools.

Success criteria:
- [x] Gate input sources defined.
- [x] Decision schema defined.
- [x] General capability dimensions documented.
- [x] Examples documented beyond weather/time-sensitive questions.
- [x] Classifier-first strategy documented.
- [x] Opt-in model-backed classifier strategy documented.
- [x] Keyword-table approach explicitly rejected for core runtime policy.

Decision dimensions:
- `needs_tool`
- `needs_live_data`
- `needs_private_context`
- `needs_external_system`
- `needs_browser_interaction`
- `needs_grounded_verification`
- `suggested_tool_classes`
- `reason`

### 4. Capability Matcher Design
Goal: map gate decisions to the currently available AtlasClaw capabilities.

Success criteria:
- [x] Tool-class taxonomy defined.
- [x] Matching logic documented.
- [x] Fallback behavior documented when no matching tool exists.
- [x] Interaction with provider tools and browser tools documented.

Supported capability classes:
- `web_search`
- `web_fetch`
- `browser`
- `provider:<type>`
- `memory`
- `session`
- `hooks_context`

### 5. Mandatory Tool Enforcement Design
Goal: define what happens when tool use is required.

Success criteria:
- [x] Enforcement modes defined.
- [x] Failure behavior defined.
- [x] Anti-fabrication rule documented.
- [x] Final answer gating documented.

Chosen rules:
- If the request is classified as tool-required, AtlasClaw must not allow an ungrounded final answer.
- The runtime may retry with a stronger instruction, route through a controlled tool-first path, or stop with an explicit explanation that verification failed.
- The model must not claim a search or lookup happened unless tool execution evidence exists.

### 6. Reasoning-Only Response Policy Design
Goal: define how the runtime handles reasoning-only responses, bounded retries, and controlled-path escalation.

Success criteria:
- [x] Reasoning-only acceptance rule documented.
- [x] Retry thresholds documented.
- [x] Controlled-path entry rules documented.
- [x] Frontend-visible runtime states documented.

Chosen rules:
- Reasoning-only content is allowed as an intermediate state, never as a valid final answer.
- Automatic retry is bounded and evented rather than silent and open-ended.
- The runtime must escalate into a controlled path after defined thresholds instead of looping indefinitely.
- Frontend-visible states must include `reasoning`, `retrying`, `waiting_for_tool`, and `controlled_path`.

### 7. Minimal Context Integration Design
Goal: define the minimum context behavior required for Phase 1 enforcement to work reliably.

Success criteria:
- [x] Minimal context integration responsibilities documented.
- [x] Tool-result prioritization requirement documented.
- [x] Boundary against the full Context Engine documented.

Chosen rules:
- Prompt guidance remains necessary but is no longer the only control.
- The policy pipeline executes before free-form answering.
- Tool results become privileged context when enforcement requires grounding.
- Full source selection/ranking/budgeting/provenance remain deferred to the Full Context Engine spec.

### 8. Full Context Engine Design
Goal: define the separate Phase 2 context orchestration system.

Success criteria:
- [x] Source registry documented.
- [x] Selection and ranking responsibilities documented.
- [x] Transformation, budgeting, and provenance documented.
- [x] Relationship to the Phase 1 policy runtime documented.

Full Context Engine responsibilities:
- explicit context source registry,
- context selection,
- context ranking and prioritization,
- context transformation,
- context budgeting and compression,
- provenance and evidence tracking,
- policy-based context assembly.

### 9. Observability and Events
Goal: make policy and later context decisions observable and reviewable.

Success criteria:
- [x] Phase 1 event taxonomy defined.
- [x] Phase 2 observability direction documented.
- [x] Hook Runtime relationship documented.
- [x] Frontend runtime visibility expectations documented.

### 10. Testing Strategy
Goal: ensure the design is concrete enough for later implementation.

Success criteria:
- [x] Unit test scope listed.
- [x] Integration test scope listed.
- [x] E2E scenarios listed.
- [x] Phase 1 and Phase 2 testing boundaries documented.

## Verification
- command: review the current runner, prompt, tools, docs, and existing context assembly; then self-review the docs for placeholders, contradictions, staging errors, and scope drift
- expected: two staged specs where Phase 1 covers runtime policy, bounded reasoning handling, frontend runtime visibility, and Phase 2 covers the full Context Engine
- actual: spec written at `docs/superpowers/specs/2026-03-31-tool-necessity-gate-design.md`; companion spec written at `docs/superpowers/specs/2026-03-31-full-context-engine-design.md`; state/task/spec reviewed for matching staged scope, terminology, runtime-response policy, and next step

## Implementation Status
- [x] Initial Phase 1 implementation was completed and verified.
- [x] Keyword-table classification was removed from the core gate in favor of explicit classifier-driven decisions plus neutral fallback.
- [x] Default runtime no longer performs a hidden model-backed gate classification on every request; model-backed classification is explicitly enabled.
- [x] Reasoning-only response policy extension implemented.
- [x] Frontend runtime-state visibility (`reasoning`, `retrying`, `waiting_for_tool`, `tool_running`, `controlled_path`) implemented.
- [x] Controlled-path escalation timing and bounded retry runtime implemented.
- [x] Post-extension code/task/spec alignment review completed.
- [x] Final standalone code review for the extension completed.

## Handoff Notes
- Expanded Phase 1 implementation now matches the updated spec, including bounded reasoning-only handling, controlled-path escalation, and retained frontend runtime visibility.
- Full Context Engine remains the next major runtime track after this completed Phase 1 delivery.
