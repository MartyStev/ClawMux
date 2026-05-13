"""Rename crm_user_id to external_user_id in mm_user

Revision ID: 006
Revises: 005
Create Date: 2026-05-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "mm_user",
        "crm_user_id",
        new_column_name="external_user_id",
        existing_type=sa.String(length=128),
        existing_nullable=True,
        comment="External user identifier used by Control-Plane API",
    )
    op.drop_index("ix_mm_user_crm_user_id", table_name="mm_user")
    op.create_index(
        "ix_mm_user_external_user_id",
        "mm_user",
        ["external_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_mm_user_external_user_id", table_name="mm_user")
    op.alter_column(
        "mm_user",
        "external_user_id",
        new_column_name="crm_user_id",
        existing_type=sa.String(length=128),
        existing_nullable=True,
        comment="External CRM user identifier",
    )
    op.create_index("ix_mm_user_crm_user_id", "mm_user", ["crm_user_id"], unique=True)
