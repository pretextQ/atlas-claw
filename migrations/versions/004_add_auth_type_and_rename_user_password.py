# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Add auth_type to users and rename password_hash to password

Revision ID: 004
Revises: 003
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("auth_type", sa.String(length=100), nullable=False, server_default="local")
        )
        batch_op.add_column(sa.Column("password", sa.String(length=255), nullable=True))

    op.execute("UPDATE users SET password = password_hash WHERE password IS NULL")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("password_hash")

    op.create_index("ix_users_auth_type", "users", ["auth_type"])


def downgrade() -> None:
    op.drop_index("ix_users_auth_type", table_name="users")

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))

    op.execute("UPDATE users SET password_hash = password WHERE password_hash IS NULL")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("password")
        batch_op.drop_column("auth_type")
