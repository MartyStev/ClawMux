"""Move role from instance to mm_user; add instance_url index

Revision ID: 005
Revises: 004
Create Date: 2026-05-12

Изменения:
  instance: DROP COLUMN role, ADD INDEX ON instance_url
  mm_user:  ADD COLUMN role VARCHAR(64) NULL
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── instance: убираем role, добавляем индекс на instance_url ─────────────
    op.drop_column("instance", "role")
    op.create_index("ix_instance_url", "instance", ["instance_url"])

    # ── mm_user: добавляем role ───────────────────────────────────────────────
    op.add_column(
        "mm_user",
        sa.Column(
            "role", sa.String(64), nullable=True,
            comment="Agent config role, e.g. 'curator', 'admin'",
        ),
    )


def downgrade() -> None:
    op.drop_column("mm_user", "role")
    op.drop_index("ix_instance_url", table_name="instance")
    op.add_column(
        "instance",
        sa.Column(
            "role", sa.String(64), nullable=True,
            comment="Роль/конфиг из root-config/configs/ (без .json)",
        ),
    )
