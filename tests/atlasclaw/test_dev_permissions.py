# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""WP-05/F-0009: the committed dev-permission allowlist must not grant
blanket approval for arbitrary execution or silent state mutation."""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).parents[2] / ".claude" / "settings.local.json"


def _allowed_rules() -> list[str]:
    if not SETTINGS_PATH.exists():
        return []
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return list(data.get("permissions", {}).get("allow", []))


def test_no_blanket_python_execution():
    """Bash(python:*) would auto-approve running any Python code."""
    assert "Bash(python:*)" not in _allowed_rules()


def test_no_approval_free_state_mutating_git_commands():
    """git pull/checkout change the working tree without review."""
    rules = _allowed_rules()
    assert not any(rule.startswith("Bash(git pull") for rule in rules)
    assert not any(rule.startswith("Bash(git checkout") for rule in rules)


def test_allowlist_only_contains_narrow_rules():
    """Every remaining rule must name a concrete, non-wildcard binary scope."""
    for rule in _allowed_rules():
        assert rule.startswith("Bash("), rule
        binary = rule[len("Bash("):].split(":", 1)[0].strip()
        # e.g. "pip show" names the binary and the read-only subcommand
        assert binary and " " in binary, f"rule too broad: {rule}"
