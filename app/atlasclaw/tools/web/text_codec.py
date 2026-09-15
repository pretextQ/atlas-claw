# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import re
from typing import Optional


_META_CHARSET_PATTERN = re.compile(
    rb"<meta[^>]+charset=['\"]?\s*([a-zA-Z0-9._\-]+)\s*['\"]?",
    flags=re.IGNORECASE,
)
_CONTENT_TYPE_CHARSET_PATTERN = re.compile(r"charset=([a-zA-Z0-9._\-]+)", flags=re.IGNORECASE)


def _normalize_encoding_name(name: str) -> str:
    return (name or "").strip().strip("\"'").lower()


def _extract_charset_from_content_type(content_type: str) -> str:
    match = _CONTENT_TYPE_CHARSET_PATTERN.search(content_type or "")
    if not match:
        return ""
    return _normalize_encoding_name(match.group(1))


def _extract_charset_from_meta(raw_bytes: bytes) -> str:
    if not raw_bytes:
        return ""
    head = raw_bytes[:4096]
    match = _META_CHARSET_PATTERN.search(head)
    if not match:
        return ""
    try:
        return _normalize_encoding_name(match.group(1).decode("ascii", errors="ignore"))
    except Exception:
        return ""


def _decode_with_quality(raw_bytes: bytes, encoding: str) -> tuple[str, float]:
    text = raw_bytes.decode(encoding, errors="replace")
    replacement_count = text.count("\ufffd")
    lexical_count = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    # Lower replacement ratio and higher lexical ratio indicate better decoding.
    replacement_ratio = replacement_count / max(1, len(text))
    lexical_ratio = lexical_count / max(1, len(text))
    quality = (1.0 - replacement_ratio) + (lexical_ratio * 0.25)
    return text, quality


def decode_http_text(
    raw_bytes: bytes,
    *,
    declared_encoding: Optional[str] = None,
    content_type: str = "",
) -> tuple[str, str]:
    """Decode HTTP bytes into internal UTF-8-compatible Python text."""
    if not raw_bytes:
        return "", "utf-8"

    candidates: list[str] = []
    for candidate in (
        _normalize_encoding_name(declared_encoding or ""),
        _extract_charset_from_content_type(content_type),
        _extract_charset_from_meta(raw_bytes),
        "utf-8",
        "utf-8-sig",
        "gb18030",
        "gbk",
        "big5",
        "latin-1",
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for encoding in candidates:
        try:
            text = raw_bytes.decode(encoding, errors="strict")
            return text, encoding
        except Exception:
            continue

    best_text = ""
    best_encoding = "utf-8"
    best_quality = float("-inf")
    for encoding in candidates:
        try:
            text, quality = _decode_with_quality(raw_bytes, encoding)
        except Exception:
            continue
        if quality > best_quality:
            best_text = text
            best_quality = quality
            best_encoding = encoding

    if best_text:
        return best_text, best_encoding

    return raw_bytes.decode("utf-8", errors="replace"), "utf-8"
