#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Fail when a tracked Python file grows past the god-file threshold.

The refactor target (docs/python-god-file-refactor-plan.md) is one file per
responsibility under ~600 lines. Files that predate that target are listed in
``GRANDFATHERED`` with their measured size: they may shrink or stay, but they
may not grow, and the list must not gain entries. Everything else is held to
``--max-lines``.

Usage:
    python scripts/check_python_file_lengths.py                # check
    python scripts/check_python_file_lengths.py --list         # report only
    python scripts/check_python_file_lengths.py --max-lines 800
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_MAX_LINES = 600

# Pre-existing god files with their measured line counts (2026-09-29
# measurement). Shrinking is always fine (update the number after an
# actual reduction); growing is a failure, and adding a NEW entry here is
# refused during review so the refactor cannot be sidestepped.
GRANDFATHERED: dict[str, int] = {
    "tests/atlasclaw/test_runner_tool_execution_contract.py": 4785,
    "app/atlasclaw/agent/runner_tool/runner_execution_prepare.py": 2219,
    "tests/atlasclaw/test_runner_tool_gate_behavior.py": 2791,
    "tests/atlasclaw/test_runner_prompt_context.py": 1871,
    "tests/atlasclaw/api/test_role_crud_api.py": 1821,
    "app/atlasclaw/api/api_routes.py": 1733,
    "app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py": 1624,
    "app/atlasclaw/agent/runner_prompt_context.py": 1525,
    "app/atlasclaw/agent/runner_tool_evidence.py": 1357,
    "app/atlasclaw/agent/runner_tool/runner_execution_flow_stream.py": 1316,
    "tests/atlasclaw/api/test_user_profile_api.py": 1303,
    "app/atlasclaw/tools/web/provider_adapters.py": 1299,
    "app/atlasclaw/agent/runner_tool/runner_tool_gate_model.py": 1258,
    "tests/atlasclaw/e2e/test_smartcmp_e2e.py": 1231,
    "tests/atlasclaw/test_md_tool_runtime.py": 1188,
    "tests/atlasclaw/test_provider_tool_groups.py": 1162,
    "tests/atlasclaw/e2e/test_runtime_routing.py": 1156,
    "tests/atlasclaw/api/test_user_crud_api.py": 1123,
    "app/atlasclaw/skills/md_tool_runtime.py": 1079,
    "app/atlasclaw/main.py": 1068,
    "app/atlasclaw/skills/registry.py": 1068,
    "app/atlasclaw/channels/manager.py": 1052,
    "tests/atlasclaw/test_md_skills.py": 1051,
    "app/atlasclaw/tools/web/fetch_tool.py": 1014,
    "app/atlasclaw/channels/handlers/wecom.py": 996,
    "app/atlasclaw/api/channels.py": 989,
    "tests/atlasclaw/test_webhook_dispatch.py": 989,
    "app/atlasclaw/channels/handlers/feishu.py": 986,
    "app/atlasclaw/agent/prompt_sections.py": 923,
    "app/atlasclaw/agent/runner_tool/runner_prepare_workflow_context.py": 892,
    "app/atlasclaw/channels/handlers/dingtalk.py": 887,
    "tests/atlasclaw/memory/test_auto_write.py": 885,
    "app/atlasclaw/api/deps_context.py": 884,
    "tests/atlasclaw/test_channel_manager.py": 883,
    "tests/atlasclaw/test_channels_api.py": 878,
    "app/atlasclaw/memory/auto_write.py": 850,
    "tests/atlasclaw/db/test_database.py": 828,
    "app/atlasclaw/agent/runner_tool/runner_execution_payload.py": 808,
    "tests/atlasclaw/test_feishu_handler.py": 806,
    "app/atlasclaw/memory/manager.py": 792,
    "tests/atlasclaw/test_standard_skill_runtime.py": 775,
    "app/atlasclaw/agent/runner_tool/runner_tool_gate_policy.py": 765,
    "app/atlasclaw/core/encryption.py": 757,
    "app/atlasclaw/agent/runtime_events.py": 753,
    "app/atlasclaw/agent/runner_tool/runner_tool_gate_routing.py": 746,
    "tests/atlasclaw/test_model_configs.py": 743,
    "app/atlasclaw/session/manager.py": 733,
    "app/atlasclaw/agent/compaction.py": 732,
    "app/atlasclaw/tools/web/provider_runtime.py": 730,
    "app/atlasclaw/api/service_provider_schemas.py": 726,
    "tests/atlasclaw/test_enterprise_channels.py": 722,
    "app/atlasclaw/api/webhook_dispatch.py": 720,
    "app/atlasclaw/tools/skill_runtime_tools.py": 700,
    "app/atlasclaw/api/services/auth_service.py": 692,
    "app/atlasclaw/api/sse.py": 680,
    "tests/atlasclaw/e2e/test_smartcmp_live_agent_e2e.py": 678,
    "app/atlasclaw/auth/guards.py": 664,
    "app/atlasclaw/agent/history_memory.py": 662,
    "tests/atlasclaw/e2e/test_smartcmp_vm_request_e2e.py": 661,
    "tests/atlasclaw/test_session_api_routes.py": 648,
    "tests/atlasclaw/test_dingtalk_handler.py": 628,
    "tests/atlasclaw/test_embed_manifest_runtime.py": 619,
    "app/atlasclaw/agent/prompt_builder.py": 617,
    "app/atlasclaw/db/orm/role.py": 607,
    "app/atlasclaw/core/config.py": 605,
    "tests/atlasclaw/test_agent_run_api.py": 603,
}


def tracked_python_files() -> list[str]:
    """Return the tracked Python files of the repository."""
    output = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def count_lines(path: Path) -> int:
    """Return the line count of a file."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def check(max_lines: int, *, report_only: bool) -> int:
    """Report and (unless report_only) enforce the line budget."""
    offenders: list[tuple[str, int]] = []
    grown: list[tuple[str, int, int]] = []

    for relative in tracked_python_files():
        path = Path(relative)
        if not path.exists():
            continue
        lines = count_lines(path)
        budget = GRANDFATHERED.get(relative)
        if budget is None:
            if lines > max_lines:
                offenders.append((relative, lines))
            continue
        if lines > budget:
            grown.append((relative, lines, budget))

    for relative, lines in sorted(offenders, key=lambda item: -item[1]):
        print(f"OVER BUDGET  {lines:5d}  {relative}")
    for relative, lines, budget in sorted(grown, key=lambda item: -item[1]):
        print(f"GREW         {lines:5d} (was {budget})  {relative}")

    grandfathered_over = {
        relative: count_lines(Path(relative))
        for relative in GRANDFATHERED
        if Path(relative).exists()
    }
    print(
        f"\n{len(offenders)} file(s) over {max_lines} lines; "
        f"{len(grown)} grandfathered file(s) grew; "
        f"{len(grandfathered_over)} grandfathered file(s) tracked."
    )

    if report_only:
        return 0
    if offenders or grown:
        print(
            "\nSplit the file along its responsibilities (see "
            "docs/python-god-file-refactor-plan.md) or update the "
            "grandfathered size after an actual reduction."
        )
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_LINES,
        help=f"Maximum allowed lines for non-grandfathered files (default {DEFAULT_MAX_LINES})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Report only; exit 0 regardless of findings",
    )
    args = parser.parse_args(argv)
    return check(args.max_lines, report_only=args.list)


if __name__ == "__main__":
    sys.exit(main())
