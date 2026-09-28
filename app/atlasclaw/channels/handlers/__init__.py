# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Built-in channel handlers.

Only the enterprise messaging handlers are shipped: the previous in-process
WebSocket/SSE/REST channel handlers were never registered by the runtime and
have been removed.
"""

from __future__ import annotations

from .dingtalk import DingTalkHandler
from .feishu import FeishuHandler
from .wecom import WeComHandler

__all__ = ["FeishuHandler", "DingTalkHandler", "WeComHandler"]
