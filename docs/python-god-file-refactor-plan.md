# Python God File Refactor Plan

目标：杜绝单文件超过 600 行的“上帝文件”，按职责拆分并保持可测试性。

## Current Scan (Tracked `*.py`, >600 lines)

- `837` `app/atlasclaw/main.py`
- `793` `app/atlasclaw/skills/registry.py`
- `708` `app/atlasclaw/agent/prompt_builder.py`
- `698` `app/atlasclaw/models/providers.py`
- `642` `app/atlasclaw/api/api_routes.py`
- `616` `tests/atlasclaw/test_md_skills.py`

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

