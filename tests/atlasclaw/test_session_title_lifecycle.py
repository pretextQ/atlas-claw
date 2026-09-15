# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import pytest

from app.atlasclaw.agent.session_titles import SessionTitleGenerator
from app.atlasclaw.session.manager import SessionManager


def test_session_title_generator_builds_draft_title():
    generator = SessionTitleGenerator(max_length=12)

    title = generator.build_draft_title("  查询待办审批流程的状态  ")

    assert title == "查询待办审批流程的状态"


def test_session_title_generator_uses_assistant_hint_for_generic_opening():
    generator = SessionTitleGenerator(max_length=18)

    title = generator.build_final_title(
        first_user_message="hello",
        first_assistant_message="关于 AtlasClaw 会话恢复机制的说明：已支持切换恢复历史。",
    )

    assert title.startswith("关于 AtlasClaw")


@pytest.mark.asyncio
async def test_session_manager_persists_title_and_status(tmp_path):
    manager = SessionManager(workspace_path=str(tmp_path), user_id="alice")
    session_key = "agent:main:user:alice:web:dm:alice:topic:thread-1"

    await manager.get_or_create(session_key)
    await manager.update_title(session_key, title="恢复聊天历史", title_status="final")

    session = await manager.get_session(session_key)

    assert session is not None
    assert session.title == "恢复聊天历史"
    assert session.title_status == "final"
