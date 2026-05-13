"""Create user_instance table

Revision ID: 001
Revises: None
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_instance",
        sa.Column("user_id", sa.String(64), primary_key=True,
                  comment="Mattermost user_id"),
        sa.Column("instance_url", sa.Text(), nullable=False,
                  comment="OpenClaw WS URL, e.g. ws://host:18789/ws"),
        sa.Column("device_id", sa.String(64), nullable=False,
                  comment="SHA-256 hex of public key"),
        sa.Column("public_key_b64", sa.Text(), nullable=False,
                  comment="Ed25519 public key, base64url no padding"),
        sa.Column("private_key_b64", sa.Text(), nullable=False,
                  comment="Ed25519 private key, base64url no padding"),
        sa.Column("device_token", sa.Text(), nullable=False,
                  comment="Operator token from paired.json, base64url no padding"),
        sa.Column("gateway_token", sa.Text(), nullable=False,
                  comment="OPENCLAW_GATEWAY_TOKEN for this specific instance"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_instance")
