"""
WS Router — SQLAlchemy models (3NF).

Таблицы:
  instance       — инстансы OpenClaw + device credentials
  app_user       — канонический пользователь внутри роутера
  user_identity  — идентичность пользователя в конкретном провайдере
  user_instance  — активная привязка пользователя к инстансу (1:1)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint, func
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


class AppUser(Base):
    """
    Канонический пользователь роутера.

    Хранит внешний идентификатор бизнес-системы и роль.
    Идентичности каналов (Mattermost/Slack/...) хранятся в user_identity.
    """

    __tablename__ = "app_user"

    id: str = Column(
        String(64), primary_key=True, comment="Internal router user identifier",
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
    identities: list["UserIdentity"] = relationship(
        "UserIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    assignment: Optional["UserInstance"] = relationship(
        "UserInstance", back_populates="user", uselist=False,
    )


class UserIdentity(Base):
    """
    Идентичность пользователя в канале/провайдере.

    Примеры:
      provider='mattermost', provider_user_id='<mattermost_user_id>'
      provider='slack',      provider_user_id='<slack_user_id>'
    """

    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_identity_user_provider"),
        # Один аккаунт пользователя на провайдера (например, один Mattermost ID)
        {"comment": "Provider identities for router users"},
    )

    user_id: str = Column(
        String(64), ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: str = Column(
        String(32), primary_key=True,
        comment="Identity provider, e.g. mattermost/slack",
    )
    provider_user_id: str = Column(
        String(128), primary_key=True, comment="User identifier inside provider",
    )
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(),
    )

    user: AppUser = relationship("AppUser", back_populates="identities")


class UserInstance(Base):
    """
    Активная привязка пользователя к инстансу (1:1).

    instance_uuid — PK и FK на instance (1 инстанс = 1 активный юзер)
    user_id       — UNIQUE FK на app_user (1 юзер = 1 активный инстанс)

    Для освобождения инстанса — удалить строку (DELETE).
    Для переназначения — сначала DELETE, потом INSERT.
    """

    __tablename__ = "user_instance"

    instance_uuid: str = Column(
        String(36), ForeignKey("instance.instance_uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: str = Column(
        String(64), ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    assigned_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    instance: Instance = relationship("Instance", back_populates="assignment")
    user: AppUser = relationship("AppUser", back_populates="assignment")
