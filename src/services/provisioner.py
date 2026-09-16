"""
ClawMux — Instance Auto-Provisioning Service.

Spawns user-dedicated OpenClaw instances automatically upon first message from an unmapped user.
Drivers supported:
  - 'webhook': Calls external orchestrator endpoint (e.g., Kubernetes/Docker manager)
  - 'mock': Generates an instant local test instance for development/testing
"""

import asyncio
import uuid
from typing import Optional

import httpx
import structlog

from src.core.config import settings
from src.services.mapping import DeviceCredentials, InstanceInfo, MappingStorage

logger = structlog.get_logger(__name__)


class ProvisioningError(Exception):
    """Raised when instance provisioning fails or times out."""
    pass


class InstanceProvisioner:
    """
    Manages lazy auto-provisioning of OpenClaw instances.
    """

    def __init__(self, mapping: MappingStorage) -> None:
        self.mapping = mapping

    async def provision_instance(
        self,
        provider: str,
        user_id: str,
    ) -> InstanceInfo:
        """
        Auto-provision an OpenClaw instance for an unmapped user and record in DB.
        """
        logger.info(
            "auto_provisioning_started",
            provider=provider,
            user_id=user_id,
            driver=settings.provisioning_driver,
        )

        driver = settings.provisioning_driver.lower().strip()
        if driver == "mock":
            info, instance_uuid = await self._provision_mock(provider, user_id)
        elif driver == "webhook":
            info, instance_uuid = await self._provision_webhook(provider, user_id)
        else:
            raise ProvisioningError(f"Unsupported provisioning driver: {driver}")

        # Bind the provisioned instance in database mapping
        return await self.mapping.bind_user_instance(
            provider=provider,
            provider_user_id=user_id,
            instance_uuid=instance_uuid,
            instance_url=info.instance_url,
            credentials=info.credentials,
        )

    async def _provision_mock(
        self,
        provider: str,
        user_id: str,
    ) -> tuple[InstanceInfo, str]:
        """Generate mock instance info instantly for testing."""
        inst_uuid = str(uuid.uuid4())
        mock_info = InstanceInfo(
            instance_url=f"ws://localhost:18789/ws",
            credentials=DeviceCredentials(
                device_id=f"dev-mock-{user_id[:8]}",
                public_key_b64="mock-public-key-b64",
                private_key_b64="mock-private-key-b64",
                device_token="mock-device-token",
                gateway_token="mock-gateway-token",
            ),
        )
        return mock_info, inst_uuid

    async def _provision_webhook(
        self,
        provider: str,
        user_id: str,
    ) -> tuple[InstanceInfo, str]:
        """Call external orchestrator HTTP endpoint to provision a container/pod."""
        if not settings.provisioning_webhook_url:
            raise ProvisioningError("PROVISIONING_WEBHOOK_URL is not configured")

        headers = {"Content-Type": "application/json"}
        if settings.provisioning_webhook_token:
            headers["Authorization"] = f"Bearer {settings.provisioning_webhook_token}"

        payload = {
            "provider": provider,
            "provider_user_id": user_id,
            "app_user_id": f"{provider}:{user_id}",
        }

        try:
            async with httpx.AsyncClient(timeout=float(settings.provisioning_timeout_sec)) as client:
                resp = await client.post(
                    settings.provisioning_webhook_url,
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                instance_uuid = data.get("instance_uuid") or str(uuid.uuid4())
                instance_url = data.get("instance_url")
                creds_raw = data.get("credentials", {})

                if not instance_url:
                    raise ProvisioningError("Webhook payload missing 'instance_url'")

                credentials = DeviceCredentials(
                    device_id=creds_raw.get("device_id", ""),
                    public_key_b64=creds_raw.get("public_key_b64", ""),
                    private_key_b64=creds_raw.get("private_key_b64", ""),
                    device_token=creds_raw.get("device_token", ""),
                    gateway_token=creds_raw.get("gateway_token", ""),
                )
                return InstanceInfo(instance_url=instance_url, credentials=credentials), instance_uuid

        except Exception as e:
            logger.error("provisioning_webhook_failed", provider=provider, user_id=user_id, error=str(e))
            raise ProvisioningError(f"Failed to provision instance via webhook: {e}") from e
