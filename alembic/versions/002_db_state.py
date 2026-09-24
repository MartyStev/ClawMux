"""Add user_channel and mapping_state tables (DB-backed router state)

Revision ID: 002
Revises: 001
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_channel",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("provider", "provider_user_id"),
        comment="Last known channel per provider identity for proactive delivery",
    )
    op.create_table(
        "mapping_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Singleton row: global mapping-cache version",
    )
    op.execute(
        sa.text("INSERT INTO mapping_state (id, version) VALUES (1, 1)")
    )


def downgrade() -> None:
    op.drop_table("mapping_state")
    op.drop_table("user_channel")
