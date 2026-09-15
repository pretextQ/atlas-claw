# Tool/Skill/Provider Minimal Toolset Plan

## Scope
- Deliver OpenClaw-style compact Skills prompt index with read-on-demand behavior.
- Expand and normalize AtlasClaw built-in tool groups.
- Support provider-injected tools and provider-defined dynamic groups (for example `group:cmp`).
- Build per-turn minimal executable toolset before model loop.
- Complete real-agent E2E verification with timing evidence.

## Steps
1. [ ] Prompt compact skills index
2. [ ] Built-in tool catalog and group expansion
3. [ ] Provider metadata and group injection
4. [ ] Minimal toolset policy pipeline + runner wiring
5. [ ] Documentation/state reconciliation + real-agent E2E report

## Verification
- command: targeted pytest per task (see implementation plan)
- command: full backend pytest
- command: real agent E2E (3-turn CMP scenario) with elapsed-time evidence
- expected: model loop uses filtered tools, provider queries resolve through provider tools, and response/timing is reportable per turn
- constraint: no minimal implementation; each step must be complete and review-passed before moving on

## Handoff Notes
- Spec: `docs/superpowers/specs/2026-04-07-tool-skill-provider-minimal-toolset-design.md`
- Plan: `docs/superpowers/plans/2026-04-07-tool-skill-provider-minimal-toolset-implementation-plan.md`
- Execution mode: inline task-by-task with double review checkpoint after each task

