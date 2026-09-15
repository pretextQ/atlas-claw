# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Authentication providers for AtlasClaw."""

from __future__ import annotations

from .provider import AuthProvider
from .registry import AuthRegistry

__all__ = [
    "AuthProvider",
    "AuthRegistry",
]
