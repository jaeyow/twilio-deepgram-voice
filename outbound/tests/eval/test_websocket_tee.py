import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from eval.websocket_tee import WebSocketTee


@pytest.fixture
def mock_ws():
    ws = MagicMock()
    ws.receive = AsyncMock(return_value={"type": "websocket.receive", "text": '{"event":"media"}'})
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_receive_returns_message(mock_ws):
    queue = asyncio.Queue()
    tee = WebSocketTee(mock_ws, queue)
    msg = await tee.receive()
    assert msg == {"type": "websocket.receive", "text": '{"event":"media"}'}


@pytest.mark.asyncio
async def test_receive_puts_message_in_queue(mock_ws):
    queue = asyncio.Queue()
    tee = WebSocketTee(mock_ws, queue)
    await tee.receive()
    queued = queue.get_nowait()
    assert queued == {"type": "websocket.receive", "text": '{"event":"media"}'}


@pytest.mark.asyncio
async def test_send_text_proxied(mock_ws):
    queue = asyncio.Queue()
    tee = WebSocketTee(mock_ws, queue)
    await tee.send_text("hello")
    mock_ws.send_text.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_close_puts_sentinel_then_closes(mock_ws):
    queue = asyncio.Queue()
    tee = WebSocketTee(mock_ws, queue)
    await tee.close()
    sentinel = queue.get_nowait()
    assert sentinel is None
    mock_ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_attribute_proxied(mock_ws):
    mock_ws.custom_attr = "hello"
    queue = asyncio.Queue()
    tee = WebSocketTee(mock_ws, queue)
    assert tee.custom_attr == "hello"
