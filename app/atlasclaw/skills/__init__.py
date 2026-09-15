# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""

Skill system module

Includes:
- registry:SkillRegistry Skill registry
- frontmatter:YAML frontmatter parser
- generator:Open-APISkill-Generator(from Open-API JSON Skills)
"""

from app.atlasclaw.skills.registry import (
    MdSkillEntry,
    SkillMetadata,
    SkillRegistry,
    validate_skill_name,
)
from app.atlasclaw.skills.frontmatter import (
    FrontmatterResult,
    parse_frontmatter,
)

__all__ = [
    "FrontmatterResult",
    "MdSkillEntry",
    "SkillMetadata",
    "SkillRegistry",
    "parse_frontmatter",
    "validate_skill_name",
]
