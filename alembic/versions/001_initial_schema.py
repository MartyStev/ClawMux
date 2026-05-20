"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance",
        sa.Column(
            "instance_uuid",
            sa.String(length=36),
            nullable=False,
            comment="UUID контейнера/директории (openclaw-gw-<UUID>)",
        ),
        sa.Column(
            "instance_url",
            sa.Text(),
            nullable=False,
            comment="OpenClaw WS URL: ws://openclaw-gw-<UUID>:18789/ws",
        ),
        sa.Column(
            "device_id",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex of Ed25519 public key",
        ),
        sa.Column(
            "public_key_b64",
            sa.Text(),
            nullable=False,
            comment="Ed25519 public key, base64url no padding",
        ),
        sa.Column(
            "private_key_b64",
            sa.Text(),
            nullable=False,
            comment="Ed25519 private key, base64url no padding",
        ),
        sa.Column(
            "device_token",
            sa.Text(),
            nullable=False,
            comment="Operator token from paired.json",
        ),
        sa.Column(
            "gateway_token",
            sa.Text(),
            nullable=False,
            comment="OPENCLAW_GATEWAY_TOKEN for this instance",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("instance_uuid"),
    )
    op.create_index("ix_instance_url", "instance", ["instance_url"], unique=False)

    op.create_table(
        "app_user",
        sa.Column(
            "id",
            sa.String(length=64),
            nullable=False,
            comment="Internal router user identifier",
        ),
        sa.Column(
            "external_user_id",
            sa.String(length=128),
            nullable=True,
            comment="External user identifier used by Control-Plane API",
        ),
        sa.Column(
            "role",
            sa.String(length=64),
            nullable=True,
            comment="Agent config role, e.g. 'curator', 'admin'",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_user_id"),
    )
    op.create_index(
        "ix_app_user_external_user_id",
        "app_user",
        ["external_user_id"],
        unique=True,
    )

    op.create_table(
        "user_identity",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            comment="Identity provider, e.g. mattermost/slack",
        ),
        sa.Column(
            "provider_user_id",
            sa.String(length=128),
            nullable=False,
            comment="User identifier inside provider",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider", "provider_user_id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identity_user_provider"),
    )
    op.create_index(
        "ix_user_identity_user_id",
        "user_identity",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "user_instance",
        sa.Column("instance_uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["instance_uuid"], ["instance.instance_uuid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instance_uuid"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_instance")
    op.drop_index("ix_user_identity_user_id", table_name="user_identity")
    op.drop_table("user_identity")
    op.drop_index("ix_app_user_external_user_id", table_name="app_user")
    op.drop_table("app_user")
    op.drop_index("ix_instance_url", table_name="instance")
    op.drop_table("instance")
