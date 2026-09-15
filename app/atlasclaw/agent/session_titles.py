# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Session title helpers for chat threads."""

from __future__ import annotations

import re
from typing import Optional


_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s,.;:!?，。！？；：、]+$")
_GENERIC_TITLES = {
    "hi",
    "hello",
    "hey",
    "test",
    "你好",
    "在吗",
    "继续",
    "随便聊聊",
    "聊聊",
}


class SessionTitleGenerator:
    """Generate draft and final chat titles."""

    def __init__(self, max_length: int = 24) -> None:
        self.max_length = max_length

    def build_draft_title(self, user_message: str) -> str:
        """Create a quick title from the first user turn."""
        cleaned = self._clean(user_message)
        if not cleaned:
            return "New Chat"
        return self._truncate(cleaned)

    def build_final_title(
        self,
        *,
        first_user_message: str,
        first_assistant_message: Optional[str] = None,
        existing_title: str = "",
    ) -> str:
        """Create a more stable title from the first turn."""
        draft = self.build_draft_title(first_user_message)
        if draft and draft.lower() not in _GENERIC_TITLES:
            return draft

        assistant_hint = self._extract_assistant_hint(first_assistant_message or "")
        if assistant_hint:
            return self._truncate(assistant_hint)

        if existing_title:
            return self._truncate(existing_title)
        return draft or "New Chat"

    def _extract_assistant_hint(self, text: str) -> str:
        cleaned = self._clean(text)
        if not cleaned:
            return ""

        first_sentence = re.split(r"[。！？!?\.]\s*", cleaned, maxsplit=1)[0]
        first_sentence = self._clean(first_sentence)
        if not first_sentence:
            return ""
        return first_sentence

    def _truncate(self, text: str) -> str:
        cleaned = self._clean(text)
        if len(cleaned) <= self.max_length:
            return cleaned
        return f"{cleaned[: self.max_length - 1].rstrip()}…"

    def _clean(self, text: str) -> str:
        cleaned = _WHITESPACE_RE.sub(" ", (text or "").strip())
        cleaned = cleaned.strip("\"'“”‘’")
        cleaned = _TRAILING_PUNCT_RE.sub("", cleaned)
        return cleaned
