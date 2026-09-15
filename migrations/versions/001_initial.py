# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Initial database schema

Revision ID: 001
Revises: 
Create Date: 2026-03-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agents table
    op.create_table(
        'agents',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), unique=True, nullable=False),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('identity', sa.JSON(), nullable=True),
        sa.Column('user', sa.JSON(), nullable=True),
        sa.Column('soul', sa.JSON(), nullable=True),
        sa.Column('memory', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_agents_name', 'agents', ['name'])

    # Create tokens table
    op.create_table(
        'tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), unique=True, nullable=False),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('base_url', sa.String(500), nullable=True),
        sa.Column('api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('priority', sa.Integer(), default=100, nullable=False),
        sa.Column('weight', sa.Integer(), default=100, nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('rate_limit_remaining', sa.Integer(), nullable=True),
        sa.Column('rate_limit_reset', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_tokens_name', 'tokens', ['name'])
    op.create_index('ix_tokens_provider', 'tokens', ['provider'])

    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(100), unique=True, nullable=False),
        sa.Column('email', sa.String(255), unique=True, nullable=True),
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('roles', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('is_admin', sa.Boolean(), default=False, nullable=False),
        sa.Column('display_name', sa.String(200), nullable=True),
        sa.Column('avatar_url', sa.String(500), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_users_username', 'users', ['username'])
    op.create_index('ix_users_email', 'users', ['email'])

    # Create channels table
    op.create_table(
        'channels',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_channels_name', 'channels', ['name'])
    op.create_index('ix_channels_type', 'channels', ['type'])
    op.create_index('ix_channels_user_id', 'channels', ['user_id'])

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.String(36), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=True),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_audit_logs_entity_type', 'audit_logs', ['entity_type'])
    op.create_index('ix_audit_logs_entity_id', 'audit_logs', ['entity_id'])
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('channels')
    op.drop_table('users')
    op.drop_table('tokens')
    op.drop_table('agents')
