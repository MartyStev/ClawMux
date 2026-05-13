"""
WS Router — SQLAlchemy models (3NF).

Три таблицы:
  instance       — инстансы OpenClaw + device credentials
  mm_user        — пользователи Mattermost + внешний идентификатор
  user_instance  — активная привязка пользователя к инстансу (1:1)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    __allow_unmapped__ = True  # legacy Column() style — no Mapped[] wrappers


class Instance(Base):
    """
    Инстанс OpenClaw.

    Хранит device credentials и URL подключения.
    Инстанс может существовать без привязки к пользователю (свободный пул).
    """

    __tablename__ = "instance"

    instance_uuid: str = Column(
        String(36), primary_key=True,
        comment="UUID контейнера/директории (openclaw-gw-<UUID>)",
    )
    instance_url: str = Column(
        Text, nullable=False,
        comment="OpenClaw WS URL: ws://openclaw-gw-<UUID>:18789/ws",
    )

    # ── Device identity ───────────────────────────────────────────────────────
    device_id: str = Column(
        String(64), nullable=False, comment="SHA-256 hex of Ed25519 public key",
    )
    public_key_b64: str = Column(
        Text, nullable=False, comment="Ed25519 public key, base64url no padding",
    )
    private_key_b64: str = Column(
        Text, nullable=False, comment="Ed25519 private key, base64url no padding",
    )
    device_token: str = Column(
        Text, nullable=False, comment="Operator token from paired.json",
    )
    gateway_token: str = Column(
        Text, nullable=False, comment="OPENCLAW_GATEWAY_TOKEN for this instance",
    )
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    assignment: Optional["UserInstance"] = relationship(
        "UserInstance", back_populates="instance", uselist=False,
    )


class MmUser(Base):
    """
    Пользователь (Mattermost + external id).

    Хранит идентификаторы. Может быть не привязан к инстансу.
    """

    __tablename__ = "mm_user"

    user_id: str = Column(
        String(64), primary_key=True, comment="Mattermost user_id",
    )
    external_user_id: Optional[str] = Column(
        String(128), nullable=True, unique=True, index=True,
        comment="External user identifier used by Control-Plane API",
    )
    role: Optional[str] = Column(
        String(64), nullable=True,
        comment="Agent config role, e.g. 'curator', 'admin'",
    )
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    assignment: Optional["UserInstance"] = relationship(
        "UserInstance", back_populates="user", uselist=False,
    )


class UserInstance(Base):
    """
    Активная привязка пользователя к инстансу (1:1).

    instance_uuid — PK и FK на instance (1 инстанс = 1 активный юзер)
    user_id       — UNIQUE FK на mm_user (1 юзер = 1 активный инстанс)

    Для освобождения инстанса — удалить строку (DELETE).
    Для переназначения — сначала DELETE, потом INSERT.
    """

    __tablename__ = "user_instance"

    instance_uuid: str = Column(
        String(36), ForeignKey("instance.instance_uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: str = Column(
        String(64), ForeignKey("mm_user.user_id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    assigned_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    instance: Instance = relationship("Instance", back_populates="assignment")
    user: MmUser = relationship("MmUser", back_populates="assignment")
