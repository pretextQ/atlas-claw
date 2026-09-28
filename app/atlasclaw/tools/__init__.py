# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Built-in tool package for AtlasClaw.

Tools are exposed through `RunContext[SkillDeps]` and share a common result
format. This package includes:

- base result and metadata models
- tool catalog and profile helpers
- truncation utilities
- runtime, filesystem, web, memory, session, and UI tools
"""

from app.atlasclaw.tools.base import ToolResult, ToolMetadata
from app.atlasclaw.tools.catalog import ToolCatalog, ToolProfile
from app.atlasclaw.tools.truncation import TruncationConfig, truncate_output, truncate_image_payload

__all__ = [
    "ToolResult",
    "ToolMetadata",
    "ToolCatalog",
    "ToolProfile",
    "TruncationConfig",
    "truncate_output",
    "truncate_image_payload",
]
