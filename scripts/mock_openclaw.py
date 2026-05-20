#!/usr/bin/env python3
"""
Simple mock OpenClaw WS server for testing clawmux.

Emulates basic OpenClaw WS protocol:
- connect.challenge → connect response
- chat.send → chat.final response
"""

import asyncio
import json
import os
import uuid
import websockets

PORT = int(os.getenv("MOCK_OPENCLAW_PORT", "18789"))

async def handle_connection(websocket):
    print("Mock OpenClaw: new connection")
    try:
        # Step 1: send challenge
        challenge = {
            "event": "connect.challenge",
            "payload": {"nonce": str(uuid.uuid4())}
        }
        await websocket.send(json.dumps(challenge))

        # Step 2: receive connect request
        raw = await websocket.recv()
        data = json.loads(raw)
        if data.get("method") == "connect":
            # Send connect success
            response = {"type": "res", "id": data["id"], "ok": True}
            await websocket.send(json.dumps(response))
            print("Mock OpenClaw: connected")

        # Step 3: handle messages
        async for raw in websocket:
            data = json.loads(raw)
            if data.get("method") == "chat.send":
                msg_id = data["id"]
                # Send chat.final
                final = {
                    "event": "chat.final",
                    "payload": {
                        "sessionKey": data["params"]["sessionKey"],
                        "runId": str(uuid.uuid4()),
                        "seq": 1,
                        "state": "final",
                        "text": f"Mock response to: {data['params']['message']}",
                        "mediaUrls": []
                    }
                }
                await websocket.send(json.dumps(final))
                print(f"Mock OpenClaw: responded to {msg_id}")

    except Exception as e:
        print(f"Mock OpenClaw error: {e}")

async def main():
    print(f"Mock OpenClaw starting on port {PORT}")
    async with websockets.serve(handle_connection, "0.0.0.0", PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())