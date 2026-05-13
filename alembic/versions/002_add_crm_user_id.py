"""Add crm_user_id to user_instance

Revision ID: 002
Revises: 001
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_instance",
        sa.Column(
            "crm_user_id",
            sa.String(128),
            nullable=True,
            comment="External CRM user identifier used by Control-Plane API",
        ),
    )
    # Unique constraint + index for fast lookup by crm_user_id
    op.create_index(
        "ix_user_instance_crm_user_id",
        "user_instance",
        ["crm_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_instance_crm_user_id", table_name="user_instance")
    op.drop_column("user_instance", "crm_user_id")
