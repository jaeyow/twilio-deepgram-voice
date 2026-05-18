import asyncio
from typing import Any

from fastapi import WebSocket


class WebSocketTee:
    """Wraps a FastAPI WebSocket and copies all received messages to a queue.

    Pipecat calls receive() on the websocket to get Twilio audio. Each call
    is forwarded to the underlying WebSocket and also put onto the eval queue,
    giving the Azure STT handler the same byte stream without any pipeline impact.
    """

    def __init__(self, ws: WebSocket, queue: asyncio.Queue) -> None:
        self._ws = ws
        self._queue = queue

    async def receive(self) -> dict:
        msg = await self._ws.receive()
        await self._queue.put(msg)
        return msg

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def send(self, data: dict) -> None:
        await self._ws.send(data)

    async def send_json(self, data: Any) -> None:
        await self._ws.send_json(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._queue.put(None)  # sentinel: tell eval handler the call is over
        await self._ws.close(code=code)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ws, name)
