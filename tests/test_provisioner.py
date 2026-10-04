from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.config import settings
from src.services.mapping import DeviceCredentials, InstanceInfo
from src.services.provisioner import InstanceProvisioner


@pytest.mark.anyio
async def test_provision_mock_driver():
    mapping = MagicMock()
    mapping.bind_user_instance = AsyncMock(
        return_value=InstanceInfo(
            instance_url="ws://localhost:18789/ws",
            credentials=DeviceCredentials("dev", "pub", "priv", "dt", "gt"),
        )
    )

    provisioner = InstanceProvisioner(mapping)
    provisioner._seed_workspace_template = AsyncMock()

    with patch.object(settings, "provisioning_driver", "mock"):
        info = await provisioner.provision_instance("telegram", "user_123")
        assert info.instance_url == "ws://localhost:18789/ws"
        mapping.bind_user_instance.assert_called_once()
        call_kwargs = mapping.bind_user_instance.call_args[1]
        assert call_kwargs["provider"] == "telegram"
        assert call_kwargs["provider_user_id"] == "user_123"
        provisioner._seed_workspace_template.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "returned_role, expected_role, workspace_seeded",
    [("sales", "sales", True), (None, "user", False)],
)
async def test_provision_webhook_driver(returned_role, expected_role, workspace_seeded):
    mapping = MagicMock()
    mapping.bind_user_instance = AsyncMock(
        return_value=InstanceInfo(
            instance_url="ws://spawned-host:18789/ws",
            credentials=DeviceCredentials("dev", "pub", "priv", "dt", "gt"),
        )
    )

    provisioner = InstanceProvisioner(mapping)
    provisioner._seed_workspace_template = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "instance_uuid": "30f2aeff-1111-2222-3333-123456789abc",
        "instance_url": "ws://spawned-host:18789/ws",
        "role": returned_role,
        "workspace_seeded": workspace_seeded,
        "credentials": {
            "device_id": "dev-123",
            "public_key_b64": "pub",
            "private_key_b64": "priv",
            "device_token": "dt",
            "gateway_token": "gt",
        },
    }

    with (
        patch.object(settings, "provisioning_driver", "webhook"),
        patch.object(settings, "provisioning_webhook_url", "http://orchestrator/api/spawn"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_resp
        info = await provisioner.provision_instance("slack", "U9999")

        assert info.instance_url == "ws://spawned-host:18789/ws"
        mapping.bind_user_instance.assert_called_once()
        call_kwargs = mapping.bind_user_instance.call_args[1]
        assert call_kwargs["instance_uuid"] == "30f2aeff-1111-2222-3333-123456789abc"
        assert call_kwargs["role"] == expected_role
        if workspace_seeded:
            provisioner._seed_workspace_template.assert_not_awaited()
        else:
            provisioner._seed_workspace_template.assert_awaited_once()
