import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.openclaw_client import DeviceCredentials, OpenClawClient, OpenClawConnectionError


def test_dispatch_sets_result_tuple_for_chat_send_rejection():
    """OpenClawClient should resolve the pending future with a tuple on res error."""
    credentials = DeviceCredentials(
        device_id="test-dev",
        public_key_b64="test-pub",
        private_key_b64="test-priv",
        device_token="test-dt",
        gateway_token="test-gt",
    )

    client = OpenClawClient("ws://test/ws", credentials)

    async def run_dispatch() -> tuple[str, list[str]]:
        pending_future = asyncio.get_running_loop().create_future()
        client._pending_future = pending_future
        client._pending_future_msg_id = "test-msg-id"

        parsed = {
            "type": "res",
            "ok": False,
            "error": "rate limited",
        }

        await client._dispatch(parsed)
        assert pending_future.done()
        return pending_future.result()

    result = asyncio.run(run_dispatch())
    assert result == ("[Error: rate limited]", [])


def _connect_identity(nonce, _credentials, _token):
    return {"client": {"id": "test"}, "device": {"nonce": nonce}, "role": "operator", "scopes": []}


def _connection_with_replies(*replies):
    ws = MagicMock()
    ws.recv = AsyncMock(side_effect=[json.dumps(reply) for reply in replies])
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.anyio
async def test_connect_preserves_rejection_when_error_details_are_null():
    credentials = DeviceCredentials("dev", "pub", "priv", "dt", "gt")
    ws = _connection_with_replies(
        {"event": "connect.challenge", "payload": {"nonce": "first"}},
        {"ok": False, "error": {"message": "bad token", "details": None}},
    )
    client = OpenClawClient("ws://test/ws", credentials)
    with (
        patch("src.services.openclaw_client._sign_connect", side_effect=_connect_identity),
        patch("src.services.openclaw_client.websockets.connect", new_callable=AsyncMock, return_value=ws) as connect,
    ):
        with pytest.raises(OpenClawConnectionError, match="bad token"):
            await client.connect()
        connect.assert_awaited_once()
        ws.close.assert_awaited_once()
        assert client._ws is None


@pytest.mark.anyio
async def test_connect_retries_with_gateway_build_id():
    credentials = DeviceCredentials("dev", "pub", "priv", "dt", "gt")
    first = _connection_with_replies(
        {"event": "connect.challenge", "payload": {"nonce": "first"}},
        {"ok": False, "error": {"details": {"gatewayBuildId": "build-42"}}},
    )
    second = _connection_with_replies(
        {"event": "connect.challenge", "payload": {"nonce": "second"}},
        {"ok": True},
    )
    client = OpenClawClient("ws://test/ws", credentials)
    with (
        patch("src.services.openclaw_client._sign_connect", side_effect=_connect_identity),
        patch("src.services.openclaw_client.websockets.connect", new_callable=AsyncMock, side_effect=[first, second]),
        patch.object(client, "_listen_loop", new_callable=AsyncMock),
    ):
        await client.connect()
        first.close.assert_awaited_once()
        request = json.loads(second.send.await_args.args[0])
        assert request["params"]["client"]["buildId"] == "build-42"
        assert request["params"]["device"]["nonce"] == "second"
        if client._listen_task is not None:
            await client._listen_task
