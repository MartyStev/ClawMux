import asyncio

from src.services.openclaw_client import OpenClawClient, DeviceCredentials


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
