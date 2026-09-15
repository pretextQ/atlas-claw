# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add service provider config table

Revision ID: 003
Revises: 002
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_provider_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_type", sa.String(100), nullable=False),
        sa.Column("instance_name", sa.String(100), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider_type", "instance_name", name="uq_provider_instance"),
    )
    op.create_index(
        "ix_service_provider_configs_provider_type",
        "service_provider_configs",
        ["provider_type"],
    )
    op.create_index(
        "ix_service_provider_configs_instance_name",
        "service_provider_configs",
        ["instance_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_provider_configs_instance_name", table_name="service_provider_configs")
    op.drop_index("ix_service_provider_configs_provider_type", table_name="service_provider_configs")
    op.drop_table("service_provider_configs")
