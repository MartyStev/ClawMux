"""
ClawMux — Instance Auto-Provisioning Service.

Spawns user-dedicated OpenClaw instances automatically upon first message from an unmapped user.
Drivers supported:
  - 'webhook': Calls external orchestrator endpoint (e.g., Kubernetes/Docker manager)
  - 'mock': Generates an instant local test instance for development/testing
"""

import asyncio
import uuid
from urllib.parse import urlparse

import httpx
import structlog

from src.core.config import settings
from src.services.mapping import DeviceCredentials, InstanceInfo, MappingStorage

logger = structlog.get_logger(__name__)


class ProvisioningError(Exception):
    """Raised when instance provisioning fails or times out."""

    pass


def _validate_provisioned_target(instance_uuid: str, instance_url: str) -> None:
    """
    Sanitize what the provisioning webhook returns before we trust it.

    Both values are attacker-influenced if the orchestrator is compromised:
    `instance_uuid` becomes a path component under WORKSPACE_BASE_PATH and
    `instance_url` receives our device/gateway tokens over WS.
    """
    try:
        uuid.UUID(instance_uuid)
    except ValueError as e:
        raise ProvisioningError(f"Provisioning returned invalid instance_uuid: {instance_uuid!r}") from e

    parsed = urlparse(instance_url)
    if parsed.scheme not in ("ws", "wss"):
        raise ProvisioningError(f"Instance URL scheme not allowed: {parsed.scheme!r} (expected ws/wss)")
    if not parsed.hostname:
        raise ProvisioningError("Instance URL has no host component")

    allowed = {h.strip().lower() for h in settings.provisioning_allowed_instance_hosts.split(",") if h.strip()}
    if allowed and parsed.hostname.lower() not in allowed:
        raise ProvisioningError(
            f"Instance URL host {parsed.hostname!r} is not in the PROVISIONING_ALLOWED_INSTANCE_HOSTS allowlist"
        )


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

        # Never trust the driver's answer: uuid becomes a path component and
        # instance_url receives our credentials over WS.
        _validate_provisioned_target(instance_uuid, info.instance_url)

        # Seed default workspace template (AGENTS.md, subagents, mcp, openclaw.json)
        await self._seed_workspace_template(
            instance_uuid=instance_uuid,
            provider=provider,
            user_id=user_id,
        )

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
            instance_url="ws://localhost:18789/ws",
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

    async def _seed_workspace_template(
        self,
        instance_uuid: str,
        provider: str,
        user_id: str,
    ) -> None:
        """
        Copy default workspace template files into the instance workspace folder,
        substituting {{USER_ID}}, {{UUID}}, {{PROVIDER}} placeholders.
        """
        import os
        import shutil

        template_dir = settings.workspace_template_path
        if not os.path.exists(template_dir):
            logger.warning("workspace_template_dir_not_found", template_dir=template_dir)
            return

        dest_dir = os.path.join(settings.workspace_base_path, instance_uuid, "workspace")
        try:
            await asyncio.to_thread(os.makedirs, dest_dir, exist_ok=True)

            def copy_and_substitute(src_root: str, dst_root: str) -> None:
                for root, _dirs, files in os.walk(src_root):
                    rel_path = os.path.relpath(root, src_root)
                    target_dir = os.path.join(dst_root, rel_path) if rel_path != "." else dst_root
                    os.makedirs(target_dir, exist_ok=True)

                    for file in files:
                        src_file = os.path.join(root, file)
                        dst_file = os.path.join(target_dir, file)

                        try:
                            with open(src_file, encoding="utf-8") as f:
                                content = f.read()
                            content = (
                                content.replace("{{USER_ID}}", user_id)
                                .replace("{{UUID}}", instance_uuid)
                                .replace("{{PROVIDER}}", provider)
                            )
                            with open(dst_file, "w", encoding="utf-8") as f:
                                f.write(content)
                        except UnicodeDecodeError:
                            shutil.copy2(src_file, dst_file)

            await asyncio.to_thread(copy_and_substitute, template_dir, dest_dir)
            logger.info("workspace_template_seeded", instance_uuid=instance_uuid, dest_dir=dest_dir)
        except Exception as e:
            logger.error("workspace_template_seed_failed", instance_uuid=instance_uuid, error=str(e))
