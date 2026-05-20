"""
ClawMux — SQLAlchemy models (3NF).

Tables:
  instance       — OpenClaw instances + device credentials
  app_user       — canonical user inside the router
  user_identity  — user identity for a specific provider
  user_instance  — active user binding to an instance (1:1)
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
    OpenClaw instance.

    Stores device credentials and connection URL.
    An instance can exist without being assigned to a user (idle pool).
    """

    __tablename__ = "instance"

    instance_uuid: str = Column(
        String(36), primary_key=True,
        comment="Container/directory UUID (openclaw-gw-<UUID>)",
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
    Canonical router user.

    Stores the external system user identifier and role.
    Channel identities (Mattermost/Slack/...) are stored in user_identity.
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
    Active user binding to an instance (1:1).

    instance_uuid — PK and FK to instance (1 instance = 1 active user)
    user_id       — UNIQUE FK to app_user (1 user = 1 active instance)

    To free the instance, delete the row (DELETE).
    To reassign, DELETE first, then INSERT.
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
