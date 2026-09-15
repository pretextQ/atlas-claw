# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from app.atlasclaw.tools.web.text_codec import decode_http_text


def test_decode_http_text_supports_gb18030_declared_encoding() -> None:
    original = "上海后天天气：多云转阴，11℃-20℃"
    raw = original.encode("gb18030")

    decoded, encoding = decode_http_text(
        raw,
        declared_encoding="gb18030",
        content_type="text/html; charset=gb18030",
    )

    assert decoded == original
    assert encoding == "gb18030"


def test_decode_http_text_can_read_meta_charset_without_header() -> None:
    html = (
        "<html><head><meta charset=\"gbk\"></head>"
        "<body>苏州周边徒步推荐：穹窿山、灵岩山、天平山</body></html>"
    )
    raw = html.encode("gbk")

    decoded, _encoding = decode_http_text(raw, declared_encoding=None, content_type="")

    assert "苏州周边徒步推荐" in decoded
    assert "穹窿山" in decoded
