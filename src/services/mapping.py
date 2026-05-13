"""
WS Router — Mapping Storage.

Provides lookup:
  user_id       → (instance_url, device_credentials)  — Mattermost routing
  external_user_id → (instance_url, device_credentials)  — Control-Plane API

Reads from tables: instance, mm_user, user_instance (join).
"""

import structlog
from asyncache import cached
from cachetools import TTLCache
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import async_session_factory
from src.core.models import Instance, MmUser, UserInstance

logger = structlog.get_logger(__name__)


class InstanceNotFoundError(Exception):
    """Raised when no OpenClaw instance is mapped for a user."""

    def __init__(self, identifier: str):
        self.identifier = identifier
        super().__init__(f"No OpenClaw instance found for {identifier!r}")


@dataclass(slots=True, frozen=True)
class DeviceCredentials:
    """Credentials needed to authenticate with an OpenClaw instance."""

    device_id: str
    public_key_b64: str
    private_key_b64: str
    device_token: str
    gateway_token: str


@dataclass(slots=True, frozen=True)
class InstanceInfo:
    """Result from mapping lookup: URL + credentials."""

    instance_url: str
    credentials: DeviceCredentials


class MappingStorage:
    """Reads user → instance mapping from PostgreSQL (3NF schema)."""

    @cached(cache=TTLCache(maxsize=1000, ttl=600))
    async def get_instance(self, user_id: str) -> InstanceInfo:
        """
        Get OpenClaw instance info for a user by Mattermost user_id.

        Args:
            user_id: Mattermost user ID.

        Returns:
            InstanceInfo with URL and device credentials.

        Raises:
            InstanceNotFoundError: if no active assignment exists.
        """
        async with async_session_factory() as session:
            stmt = (
                select(Instance)
                .join(UserInstance, UserInstance.instance_uuid == Instance.instance_uuid)
                .where(UserInstance.user_id == user_id)
            )
            result = await session.execute(stmt)
            instance = result.scalar_one_or_none()

            if instance is None:
                logger.warning("instance_not_found", user_id=user_id)
                raise InstanceNotFoundError(user_id)

            logger.debug(
                "instance_resolved",
                user_id=user_id,
                instance_url=instance.instance_url,
            )
            return InstanceInfo(
                instance_url=instance.instance_url,
                credentials=DeviceCredentials(
                    device_id=instance.device_id,
                    public_key_b64=instance.public_key_b64,
                    private_key_b64=instance.private_key_b64,
                    device_token=instance.device_token,
                    gateway_token=instance.gateway_token,
                ),
            )

    @cached(cache=TTLCache(maxsize=1000, ttl=600))
    async def get_instance_by_external_id(self, external_user_id: str) -> tuple[str, InstanceInfo]:
        """
        Get OpenClaw instance info for a user by external user ID.

        Args:
            external_user_id: External user identifier.

        Returns:
            Tuple of (mattermost_user_id, InstanceInfo).

        Raises:
            InstanceNotFoundError: if no mapping exists.
        """
        async with async_session_factory() as session:
            stmt = (
                select(MmUser, Instance)
                .join(UserInstance, UserInstance.user_id == MmUser.user_id)
                .join(Instance, Instance.instance_uuid == UserInstance.instance_uuid)
                .where(MmUser.external_user_id == external_user_id)
            )
            result = await session.execute(stmt)
            row = result.one_or_none()

            if row is None:
                logger.warning(
                    "instance_not_found_by_external_id",
                    external_user_id=external_user_id,
                )
                raise InstanceNotFoundError(external_user_id)

            user, instance = row

            logger.debug(
                "instance_resolved_by_external_id",
                external_user_id=external_user_id,
                user_id=user.user_id,
                instance_url=instance.instance_url,
            )
            return user.user_id, InstanceInfo(
                instance_url=instance.instance_url,
                credentials=DeviceCredentials(
                    device_id=instance.device_id,
                    public_key_b64=instance.public_key_b64,
                    private_key_b64=instance.private_key_b64,
                    device_token=instance.device_token,
                    gateway_token=instance.gateway_token,
                ),
            )
