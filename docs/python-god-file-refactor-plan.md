# Python God File Refactor Plan

目标：杜绝单文件超过 600 行的“上帝文件”，按职责拆分并保持可测试性。

## Current Scan (Tracked `*.py`, >600 lines)

Measured after the 2026-09 correctness campaign:

| Lines | File |
|---|---|
| 3186 | `app/atlasclaw/agent/runner_tool/runner_execution_prepare.py` |
| 1733 | `app/atlasclaw/api/api_routes.py` |
| 1624 | `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py` |
| 1525 | `app/atlasclaw/agent/runner_prompt_context.py` |
| 1341 | `app/atlasclaw/agent/runner_tool/runner_execution_flow_stream.py` |
| 1324 | app/atlasclaw/skills/registry.py |
| 1027 | `app/atlasclaw/agent/runner_tool/runner_tool_gate_model.py` |
| 1005 | `app/atlasclaw/main.py` |

Frontend (not covered by the Python guard):

| Lines | File |
|---|---|
| 4039 | `app/frontend/scripts/chat-ui.js` |
| 3075 | `app/frontend/scripts/pages/channels.js` |

Note: the previous version of this document listed much smaller numbers
because it counted a stale checkout. Line counts drift with every change —
run `python scripts/check_python_file_lengths.py` for the current state; the
`--max-lines` argument defines the threshold (default 600).

## Refactor Strategy by File

### `app/atlasclaw/main.py`
- Split startup helper functions into `app/atlasclaw/bootstrap/runtime_bootstrap.py`.
- Keep `main.py` focused on `lifespan()` wiring + `create_app()`.
- Move token/provider/db bootstrap helpers out first (largest non-route chunk).

### `app/atlasclaw/skills/registry.py`
- Split Markdown-skill loading into `app/atlasclaw/skills/md_loader.py`.
- Split executable skill registration/schema extraction into `app/atlasclaw/skills/executable_registry.py`.
- Keep `SkillRegistry` as thin orchestration facade.

### `app/atlasclaw/agent/prompt_builder.py`
- Split section rendering methods into `app/atlasclaw/agent/prompt_sections.py`.
- Keep `PromptBuilder.build()` as composition pipeline only.
- Move context introspection helpers (`get_context_*`) into `prompt_debug.py`.

### `app/atlasclaw/models/providers.py`
- Move provider/model presets into `app/atlasclaw/models/provider_presets.py`.
- Move `ProviderRegistry` into `provider_registry.py`.
- Move `ModelFactory` + parsing helpers into `model_factory.py`.

### `app/atlasclaw/api/api_routes.py`
- Continue splitting endpoint domains into dedicated route modules:
  - `agent_config_routes.py`
  - `token_config_routes.py`
  - `service_provider_routes.py`
  - `model_config_routes.py`
  - `user_routes.py`
- Keep `api_routes.py` as compatibility aggregator router only.

### `tests/atlasclaw/test_md_skills.py`
- Split by concern:
  - naming/validation
  - discovery/loading
  - snapshots/integration behavior
- Keep each file under 400-500 lines for readability.

## Guardrail

- Script: `scripts/check_python_file_lengths.py`
- Suggested CI command:

```bash
python scripts/check_python_file_lengths.py --max-lines 600
```

