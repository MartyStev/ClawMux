"""Refactor to 3NF: split user_instance into 3 tables

Revision ID: 004
Revises: 003
Create Date: 2026-05-12

Было:  одна денормализованная таблица user_instance (PK=user_id)
Стало:
  - instance       — инстансы OpenClaw + device credentials (PK=instance_uuid)
  - mm_user        — пользователи Mattermost/CRM (PK=user_id)
  - user_instance  — таблица связи 1:1 (instance_uuid FK, user_id FK)

Это позволяет:
  - Регистрировать инстансы без привязки к пользователю (свободный пул)
  - Переназначать инстансы между пользователями
  - Хранить историю назначений (добавив archived флаг в будущем)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 0. Переименовываем старую таблицу ПЕРВОЙ (освобождаем имя) ────────────
    op.drop_index("ix_user_instance_crm_user_id", table_name="user_instance")
    op.rename_table("user_instance", "user_instance_legacy")

    # ── 1. Таблица инстансов ──────────────────────────────────────────────────
    op.create_table(
        "instance",
        sa.Column(
            "instance_uuid", sa.String(36), primary_key=True,
            comment="UUID контейнера и директории (openclaw-gw-<UUID>)",
        ),
        sa.Column(
            "role", sa.String(64), nullable=True,
            comment="Роль/конфиг из root-config/configs/ (без .json)",
        ),
        sa.Column(
            "instance_url", sa.Text(), nullable=False,
            comment="OpenClaw WS URL: ws://openclaw-gw-<UUID>:18789/ws",
        ),
        sa.Column("device_id", sa.String(64), nullable=False,
                  comment="SHA-256 hex of Ed25519 public key"),
        sa.Column("public_key_b64", sa.Text(), nullable=False,
                  comment="Ed25519 public key, base64url no padding"),
        sa.Column("private_key_b64", sa.Text(), nullable=False,
                  comment="Ed25519 private key, base64url no padding"),
        sa.Column("device_token", sa.Text(), nullable=False,
                  comment="Operator token from paired.json"),
        sa.Column("gateway_token", sa.Text(), nullable=False,
                  comment="OPENCLAW_GATEWAY_TOKEN for this instance"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )

    # ── 2. Таблица пользователей ──────────────────────────────────────────────
    op.create_table(
        "mm_user",
        sa.Column(
            "user_id", sa.String(64), primary_key=True,
            comment="Mattermost user_id",
        ),
        sa.Column(
            "crm_user_id", sa.String(128), nullable=True, unique=True,
            comment="External CRM user identifier",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_mm_user_crm_user_id", "mm_user", ["crm_user_id"], unique=True)

    # ── 3. Таблица связи (активное назначение 1:1) ────────────────────────────
    # instance_uuid — PK (один инстанс — один активный юзер)
    # user_id       — UNIQUE (один юзер — один активный инстанс)
    op.create_table(
        "user_instance",
        sa.Column(
            "instance_uuid", sa.String(36),
            sa.ForeignKey("instance.instance_uuid", ondelete="CASCADE"),
            primary_key=True,
            comment="FK -> instance",
        ),
        sa.Column(
            "user_id", sa.String(64),
            sa.ForeignKey("mm_user.user_id", ondelete="CASCADE"),
            nullable=False, unique=True,
            comment="FK -> mm_user",
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_user_instance_user_id", "user_instance", ["user_id"], unique=True)

    # ── 4. Перенос данных из legacy ───────────────────────────────────────────
    # Заполняем instance (instance_uuid извлекаем из instance_url)
    op.execute("""
        INSERT INTO instance (
            instance_uuid, role, instance_url,
            device_id, public_key_b64, private_key_b64,
            device_token, gateway_token, created_at
        )
        SELECT
            regexp_replace(
                instance_url,
                '^ws://openclaw-gw-([0-9a-f-]+):[0-9]+/ws$',
                '\\1'
            ) AS instance_uuid,
            NULL AS role,
            instance_url, device_id, public_key_b64, private_key_b64,
            device_token, gateway_token, created_at
        FROM user_instance_legacy
    """)

    # Заполняем mm_user из legacy
    op.execute("""
        INSERT INTO mm_user (user_id, crm_user_id, created_at)
        SELECT user_id, crm_user_id, created_at
        FROM user_instance_legacy
    """)

    # Заполняем таблицу связи
    op.execute("""
        INSERT INTO user_instance (instance_uuid, user_id, assigned_at)
        SELECT
            regexp_replace(
                instance_url,
                '^ws://openclaw-gw-([0-9a-f-]+):[0-9]+/ws$',
                '\\1'
            ) AS instance_uuid,
            user_id,
            created_at
        FROM user_instance_legacy
    """)

    # ── 5. Удаляем legacy ─────────────────────────────────────────────────────
    op.drop_table("user_instance_legacy")


def downgrade() -> None:
    # Восстанавливаем денормализованную схему из 002
    op.create_table(
        "user_instance_restore",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("crm_user_id", sa.String(128), nullable=True),
        sa.Column("instance_url", sa.Text(), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("public_key_b64", sa.Text(), nullable=False),
        sa.Column("private_key_b64", sa.Text(), nullable=False),
        sa.Column("device_token", sa.Text(), nullable=False),
        sa.Column("gateway_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.execute("""
        INSERT INTO user_instance_restore
        SELECT
            ui.user_id, u.crm_user_id,
            i.instance_url, i.device_id, i.public_key_b64, i.private_key_b64,
            i.device_token, i.gateway_token, i.created_at
        FROM user_instance ui
        JOIN instance i USING (instance_uuid)
        JOIN mm_user u USING (user_id)
    """)
    op.drop_index("ix_user_instance_user_id", table_name="user_instance")
    op.drop_table("user_instance")
    op.drop_index("ix_mm_user_crm_user_id", table_name="mm_user")
    op.drop_table("mm_user")
    op.drop_table("instance")
    op.rename_table("user_instance_restore", "user_instance")
    op.create_index("ix_user_instance_crm_user_id", "user_instance",
                    ["crm_user_id"], unique=True)
