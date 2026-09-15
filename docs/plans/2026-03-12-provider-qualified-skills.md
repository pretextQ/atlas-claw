# Provider-Qualified Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all provider skills load as `provider:skill` from `providers_root` and remove the separate webhook skill source configuration.

**Architecture:** AtlasClaw will derive a stable provider namespace from each provider directory under `providers_root`, pass that namespace into markdown skill loading, and rely on the resulting qualified names for webhook dispatch. Webhook startup validation will stop depending on `webhook.skill_sources` because provider skills are already in the registry.

**Tech Stack:** Python, FastAPI, Pydantic, JSON config, pytest

---

### Task 1: Remove webhook-specific skill source configuration

**Files:**
- Modify: `atlasclaw/app/atlasclaw/core/config_schema.py`
- Modify: `atlasclaw/app/atlasclaw/api/webhook_dispatch.py`
- Modify: `atlasclaw/atlasclaw.json`
- Modify: `atlasclaw/atlasclaw.json.example`

**Step 1: Remove `WebhookSkillSourceConfig` and `WebhookConfig.skill_sources`.**

**Step 2: Delete webhook startup validation that requires `skill_sources`.**

**Step 3: Remove `skill_sources` from shipped config files.**

### Task 2: Load provider skills with qualified names from `providers_root`

**Files:**
- Modify: `atlasclaw/app/atlasclaw/main.py`

**Step 1: Add a helper that normalizes provider directory names into provider namespaces.**

**Step 2: Pass the derived namespace to `SkillRegistry.load_from_directory(..., provider=...)` when loading provider skills.**

**Step 3: Remove obsolete webhook extra loading logic.**

**Step 4: Update the startup notice to reference `providers_root` instead of the removed built-in provider path.**

### Task 3: Update tests and docs

**Files:**
- Modify: `atlasclaw/tests/atlasclaw/test_main_startup.py`
- Modify: `atlasclaw/tests/atlasclaw/test_webhook_dispatch.py`
- Modify: `atlasclaw/README.md`
- Modify: `atlasclaw-providers/README.md`

**Step 1: Update tests to expect `jira:jira-issue` and remove `skill_sources` setup.**

**Step 2: Update docs and examples so webhook configuration only uses `allowed_skills` with qualified names.**

### Task 4: Verify behavior

**Files:**
- Test: `atlasclaw/tests/atlasclaw/test_main_startup.py`
- Test: `atlasclaw/tests/atlasclaw/test_webhook_dispatch.py`

**Step 1: Run config JSON validation.**

Run: `python3 - <<'PY'\nimport json\njson.load(open('atlasclaw/atlasclaw.json'))\njson.load(open('atlasclaw/atlasclaw.json.example'))\nprint('json-ok')\nPY`

Expected: `json-ok`

**Step 2: Run focused tests.**

Run: `pytest tests/atlasclaw/test_main_startup.py tests/atlasclaw/test_webhook_dispatch.py -q`

Expected: passing tests

**Step 3: Run syntax validation.**

Run: `python3 -m compileall atlasclaw/app/atlasclaw/main.py atlasclaw/app/atlasclaw/api/webhook_dispatch.py atlasclaw/app/atlasclaw/core/config_schema.py`

Expected: successful compilation with no errors
