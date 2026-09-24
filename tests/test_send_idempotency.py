"""Send idempotency: one idempotencyKey per logical send, reused on retry."""

import asyncio
import json

from src.services.mapping import DeviceCredentials, InstanceInfo
from src.services.openclaw_client import OpenClawClient
from src.services import ws_manager as ws_mod


# ── Client level: real OpenClawClient.send_message against a fake WS ──


class _FakeState:
    name = "OPEN"


class _FakeProtocol:
    state = _FakeState()


class _FakeWS:
    """Captures chat.send payloads and immediately resolves the pending future."""

    def __init__(self, client: OpenClawClient):
        self._client = client
        self.sent: list[dict] = []
        self.protocol = _FakeProtocol()

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        future = self._client._pending_future
        if future is not None and not future.done():
            future.set_result(("ok", []))


def _make_client() -> tuple[OpenClawClient, _FakeWS]:
    credentials = DeviceCredentials(
        device_id="dev-1",
        public_key_b64="pub",
        private_key_b64="priv",
        device_token="dt",
        gateway_token="gt",
    )
    client = OpenClawClient("ws://test/ws", credentials)
    ws = _FakeWS(client)
    client._ws = ws
    client._connected = True
    # is_connected also requires a live listen task
    client._listen_task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    return client, ws


async def _stop_client(client: OpenClawClient) -> None:
    client._listen_task.cancel()
    try:
        await client._listen_task
    except asyncio.CancelledError:
        pass


def test_client_uses_supplied_idempotency_key():
    async def run():
        client, ws = _make_client()
        try:
            await client.send_message("hi", idempotency_key="key-123")
            assert ws.sent[0]["params"]["idempotencyKey"] == "key-123"
        finally:
            await _stop_client(client)

    asyncio.run(run())


def test_client_generates_unique_keys_when_not_supplied():
    async def run():
        client, ws = _make_client()
        try:
            await client.send_message("one")
            await client.send_message("two")
            k1 = ws.sent[0]["params"]["idempotencyKey"]
            k2 = ws.sent[1]["params"]["idempotencyKey"]
            assert k1 and k2 and k1 != k2
        finally:
            await _stop_client(client)

    asyncio.run(run())


# ── Manager level: real WSConnectionManager against fake clients ──


class _FlakyClient:
    """Stand-in for OpenClawClient.

    The first client ever created fails its first send once (mid-request WS
    drop); every other call succeeds after a short await.
    """

    created: list["_FlakyClient"] = []

    def __init__(self, instance_url, credentials, on_proactive=None):
        self.is_connected = True
        self.closed = False
        self.attempt_keys: list[str] = []
        self.concurrent_now = 0
        self.max_concurrent = 0
        _FlakyClient.created.append(self)

    async def connect(self):
        pass

    async def close(self):
        self.closed = True
        self.is_connected = False

    async def send_message(self, message, session_key="agent:main:main",
                           on_stream=None, idempotency_key=None):
        self.attempt_keys.append(idempotency_key)
        self.concurrent_now += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent_now)
        try:
            await asyncio.sleep(0.01)
            if self is _FlakyClient.created[0] and len(self.attempt_keys) == 1:
                raise ConnectionError("WS dropped mid-request")
            return ("response", [])
        finally:
            self.concurrent_now -= 1


def _make_info() -> InstanceInfo:
    credentials = DeviceCredentials(
        device_id="dev-1",
        public_key_b64="pub",
        private_key_b64="priv",
        device_token="dt",
        gateway_token="gt",
    )
    return InstanceInfo(instance_url="ws://test/ws", credentials=credentials)


def test_retry_reuses_same_idempotency_key(monkeypatch):
    async def run():
        _FlakyClient.created = []
        monkeypatch.setattr(ws_mod, "OpenClawClient", _FlakyClient)
        mgr = ws_mod.WSConnectionManager()
        try:
            text, _ = await mgr.send_message("user-1", _make_info(), "hello")
            assert text == "response"
            first, second = _FlakyClient.created
            assert first is not second  # old client replaced after the drop
            assert first.attempt_keys == [second.attempt_keys[0]]
            assert first.attempt_keys[0] is not None
        finally:
            await mgr.close_all()

    asyncio.run(run())


def test_concurrent_sends_are_serialized_per_user(monkeypatch):
    async def run():
        _FlakyClient.created = []
        monkeypatch.setattr(ws_mod, "OpenClawClient", _FlakyClient)
        mgr = ws_mod.WSConnectionManager()
        try:
            results = await asyncio.gather(
                mgr.send_message("user-1", _make_info(), "a"),
                mgr.send_message("user-1", _make_info(), "b"),
            )
            assert [r[0] for r in results] == ["response", "response"]
            for client in _FlakyClient.created:
                assert client.max_concurrent == 1
        finally:
            await mgr.close_all()

    asyncio.run(run())


def test_each_logical_send_gets_a_fresh_key(monkeypatch):
    async def run():
        _FlakyClient.created = []
        monkeypatch.setattr(ws_mod, "OpenClawClient", _FlakyClient)
        mgr = ws_mod.WSConnectionManager()
        try:
            await mgr.send_message("user-1", _make_info(), "a")
            await mgr.send_message("user-1", _make_info(), "b")
        finally:
            await mgr.close_all()
        # send "a": attempt on client1 (fails) + retry on client2 = same key;
        # send "b": one attempt on client2 = new key. 3 uses, 2 distinct keys.
        all_keys = [k for c in _FlakyClient.created for k in c.attempt_keys]
        assert len(all_keys) == 3
        assert len(set(all_keys)) == 2

    asyncio.run(run())
