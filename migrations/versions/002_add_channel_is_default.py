# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add is_default to channels

Revision ID: 002
Revises: 001
Create Date: 2026-03-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_default column to channels table
    op.add_column(
        'channels',
        sa.Column('is_default', sa.Boolean(), default=False, nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('channels', 'is_default')
