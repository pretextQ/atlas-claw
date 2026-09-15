# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Built-in tool package for AtlasClaw.

Tools are exposed through `RunContext[SkillDeps]` and share a common result
format. This package includes:

- base result and metadata models
- tool catalog and profile helpers
- approval and truncation utilities
- runtime, filesystem, web, memory, session, and UI tools
"""

from app.atlasclaw.tools.base import ToolResult, ToolMetadata
from app.atlasclaw.tools.catalog import ToolCatalog, ToolProfile
from app.atlasclaw.tools.approval import ApprovalManager, ApprovalPolicy, ApprovalRequest
from app.atlasclaw.tools.truncation import TruncationConfig, truncate_output, truncate_image_payload

__all__ = [
    "ToolResult",
    "ToolMetadata",
    "ToolCatalog",
    "ToolProfile",
    "ApprovalManager",
    "ApprovalPolicy",
    "ApprovalRequest",
    "TruncationConfig",
    "truncate_output",
    "truncate_image_payload",
]
