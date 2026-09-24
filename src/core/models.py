"""
ClawMux — SQLAlchemy models (3NF).

Tables:
  instance       — OpenClaw instances + device credentials
  app_user       — canonical user inside the router
  user_identity  — user identity for a specific provider
  user_instance  — active user binding to an instance (1:1)
  user_channel   — last known channel per provider identity (proactive delivery)
  mapping_state  — global cache-version counter for cross-replica cache busting
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.core.crypto import EncryptedText


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class Instance(Base):
    """
    OpenClaw instance.

    Stores device credentials and connection URL.
    An instance can exist without being assigned to a user (idle pool).
    """

    __tablename__ = "instance"

    instance_uuid: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="Container/directory UUID (openclaw-gw-<UUID>)",
    )
    instance_url: Mapped[str] = mapped_column(
        Text,
        comment="OpenClaw WS URL: ws://openclaw-gw-<UUID>:18789/ws",
    )

    # ── Device identity ───────────────────────────────────────────────────────
    device_id: Mapped[str] = mapped_column(
        String(64),
        comment="SHA-256 hex of Ed25519 public key",
    )
    public_key_b64: Mapped[str] = mapped_column(
        Text,
        comment="Ed25519 public key, base64url no padding",
    )
    private_key_b64: Mapped[str] = mapped_column(
        EncryptedText,
        comment="Ed25519 private key, base64url no padding (encrypted at rest)",
    )
    device_token: Mapped[str] = mapped_column(
        EncryptedText,
        comment="Operator token from paired.json (encrypted at rest)",
    )
    gateway_token: Mapped[str] = mapped_column(
        EncryptedText,
        comment="OPENCLAW_GATEWAY_TOKEN for this instance (encrypted at rest)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    assignment: Mapped[Optional["UserInstance"]] = relationship(
        "UserInstance",
        back_populates="instance",
        uselist=False,
    )


class AppUser(Base):
    """
    Canonical router user.

    Stores the external system user identifier and role.
    Channel identities (Mattermost/Slack/...) are stored in user_identity.
    """

    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="Internal router user identifier",
    )
    external_user_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
        index=True,
        comment="External user identifier used by Control-Plane API",
    )
    role: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Agent config role, e.g. 'curator', 'admin'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    identities: Mapped[list["UserIdentity"]] = relationship(
        "UserIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    assignment: Mapped[Optional["UserInstance"]] = relationship(
        "UserInstance",
        back_populates="user",
        uselist=False,
    )


class UserIdentity(Base):
    """
    User identity in a channel/provider.

    Examples:
      provider='mattermost', provider_user_id='<mattermost_user_id>'
      provider='slack',      provider_user_id='<slack_user_id>'
    """

    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_identity_user_provider"),
        # One user account per provider (for example, one Mattermost ID)
        {"comment": "Provider identities for router users"},
    )

    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("app_user.id", ondelete="CASCADE"),
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        comment="Identity provider, e.g. mattermost/slack",
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        comment="User identifier inside provider",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="identities")


class UserInstance(Base):
    """
    Active user binding to an instance (1:1).

    instance_uuid — PK and FK to instance (1 instance = 1 active user)
    user_id       — UNIQUE FK to app_user (1 user = 1 active instance)

    To free the instance, delete the row (DELETE).
    To reassign, DELETE first, then INSERT.
    """

    __tablename__ = "user_instance"

    instance_uuid: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("instance.instance_uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        unique=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    instance: Mapped["Instance"] = relationship("Instance", back_populates="assignment")
    user: Mapped["AppUser"] = relationship("AppUser", back_populates="assignment")


class UserChannel(Base):
    """
    Last known channel for a provider identity (composite PK).

    Persisted so proactive delivery survives restarts and works across replicas.
    """

    __tablename__ = "user_channel"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class MappingState(Base):
    """
    Singleton row (id=1) holding the global mapping-cache version.

    Every mapping mutation bumps it; readers compare it against the version
    their cached entry was produced with and reload on mismatch — so cache
    invalidation works across processes/replicas, not just locally.
    """

    __tablename__ = "mapping_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
