# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.core.config_schema import AtlasClawConfig


def test_hooks_runtime_config_parses_script_handlers() -> None:
    config = AtlasClawConfig.model_validate(
        {
            "hooks_runtime": {
                "script_handlers": [
                    {
                        "module": "script-audit",
                        "events": ["run.context_ready", "run.failed"],
                        "command": ["python", "scripts/hook.py"],
                        "timeout_seconds": 15,
                        "enabled": True,
                        "cwd": ".",
                        "priority": 50,
                    }
                ]
            }
        }
    )

    assert len(config.hooks_runtime.script_handlers) == 1
    handler = config.hooks_runtime.script_handlers[0]
    assert handler.module == "script-audit"
    assert handler.events == ["run.context_ready", "run.failed"]
    assert handler.command == ["python", "scripts/hook.py"]
    assert handler.timeout_seconds == 15
    assert handler.enabled is True
    assert handler.cwd == "."
    assert handler.priority == 50
