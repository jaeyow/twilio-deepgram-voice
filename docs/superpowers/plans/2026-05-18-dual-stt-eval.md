# Dual STT Eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Azure STT in parallel with the live Pipecat/Deepgram pipeline to shadow-transcribe every call, compare the two transcripts at call end, and emit agreement signals via OpenTelemetry.

**Architecture:** A `WebSocketTee` wraps the FastAPI WebSocket at `/ws`, copying every incoming Twilio message into an asyncio queue while passing it through to Pipecat unchanged. `run_azure_stt_from_queue` reads from that queue and streams audio to Azure STT concurrently. A `TranscriptionCapture` pass-through frame processor captures Deepgram transcriptions from the pipeline. At call end, `Comparator` normalises and diffs both transcripts; `OtelEmitter` sends gauges and a span to the OTel collector.

**Deviation from spec:** The spec assumed Twilio natively supports multiple `<Stream>` URLs in one `<Connect>`. Twilio's `<Stream>` only supports one destination URL per `<Connect>` verb. The in-process WebSocket queue tee achieves the same goal (parallel, zero pipeline-latency impact) more reliably. No TwiML change is required.

**Tech Stack:** Python 3.12, FastAPI, Pipecat ≥ 0.0.99, Azure Cognitive Services Speech SDK, OpenTelemetry Python SDK, pytest, audioop (stdlib, available in Python 3.12)

---

## File Map

**New files:**
- `outbound/eval/__init__.py` — makes eval a package
- `outbound/eval/transcript_store.py` — thread-safe in-memory dict keyed by call_sid
- `outbound/eval/comparator.py` — normalise + SequenceMatcher + word diff
- `outbound/eval/websocket_tee.py` — WebSocket wrapper that copies received messages to a queue
- `outbound/eval/transcription_capture.py` — Pipecat FrameProcessor that captures TranscriptionFrames
- `outbound/eval/otel_emitter.py` — OTel metrics + span emission
- `outbound/eval/azure_stt_handler.py` — reads queue, streams mulaw→PCM to Azure STT, triggers comparison
- `outbound/tests/__init__.py`
- `outbound/tests/eval/__init__.py`
- `outbound/tests/eval/test_transcript_store.py`
- `outbound/tests/eval/test_comparator.py`
- `outbound/tests/eval/test_websocket_tee.py`
- `outbound/tests/eval/test_otel_emitter.py`

**Modified files:**
- `outbound/pyproject.toml` — add azure-cognitiveservices-speech, opentelemetry-api/sdk, pytest
- `outbound/server.py` — update `/ws` to use `WebSocketTee` + `asyncio.gather` with eval task
- `outbound/modal_app.py` — same `/ws` change + add eval files to Modal image
- `outbound/bot.py` — insert `TranscriptionCapture` between `stt` and `user_aggregator`

---

## Task 1: Add Dependencies

**Files:**
- Modify: `outbound/pyproject.toml`

- [ ] **Step 1: Write a dependency smoke test**

Create `outbound/tests/eval/test_imports.py`:

```python
def test_azure_speech_import():
    import azure.cognitiveservices.speech  # noqa: F401

def test_otel_import():
    from opentelemetry import metrics, trace  # noqa: F401
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd outbound && uv run pytest tests/eval/test_imports.py -v
```

Expected: `ModuleNotFoundError: No module named 'azure.cognitiveservices.speech'`

- [ ] **Step 3: Add dependencies**

Replace the `[project]` section, `[dependency-groups]`, and add a pytest config section in `outbound/pyproject.toml`:

```toml
[project]
name = "twilio-chatbot-dial-out"
version = "0.1.0"
description = "Twilio dial-out example for Pipecat"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "pipecat-ai[websocket,groq,silero,deepgram,rnnoise,runner,local-smart-turn-v3]>=0.0.99",
    "pipecatcloud>=0.2.18",
    "twilio",
    "requests",
    "azure-cognitiveservices-speech>=1.38.0",
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
]

[dependency-groups]
dev = [
    "pyright>=1.1.404,<2",
    "ruff>=0.12.11,<1",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 4: Install**

```bash
cd outbound && uv sync
```

Expected: resolves and installs without error.

- [ ] **Step 5: Run smoke test**

```bash
cd outbound && uv run pytest tests/eval/test_imports.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Create package init files**

```bash
touch outbound/eval/__init__.py outbound/tests/__init__.py outbound/tests/eval/__init__.py
```

- [ ] **Step 7: Delete the smoke test file** (it served its purpose)

```bash
rm outbound/tests/eval/test_imports.py
```

- [ ] **Step 8: Commit**

```bash
cd outbound && git add pyproject.toml eval/__init__.py tests/__init__.py tests/eval/__init__.py
git commit -m "feat(eval): add azure-speech and opentelemetry dependencies"
```

---

## Task 2: Transcript Store

**Files:**
- Create: `outbound/eval/transcript_store.py`
- Create: `outbound/tests/eval/test_transcript_store.py`

- [ ] **Step 1: Write failing tests**

Create `outbound/tests/eval/test_transcript_store.py`:

```python
import pytest
from eval import transcript_store


@pytest.fixture(autouse=True)
def cleanup():
    yield
    transcript_store.remove("CA_test")


def test_get_or_create_initialises_empty():
    entry = transcript_store.get_or_create("CA_test")
    assert entry["deepgram"] == []
    assert entry["azure"] == []
    assert entry["audio_bytes"] == 0


def test_get_returns_none_for_unknown():
    assert transcript_store.get("CA_unknown") is None


def test_append_deepgram_stores_utterance():
    transcript_store.get_or_create("CA_test")
    transcript_store.append_deepgram("CA_test", "hello world")
    entry = transcript_store.get("CA_test")
    assert entry["deepgram"] == ["hello world"]


def test_append_azure_stores_utterance():
    transcript_store.get_or_create("CA_test")
    transcript_store.append_azure("CA_test", "hi there")
    entry = transcript_store.get("CA_test")
    assert entry["azure"] == ["hi there"]


def test_add_audio_bytes_accumulates():
    transcript_store.get_or_create("CA_test")
    transcript_store.add_audio_bytes("CA_test", 800)
    transcript_store.add_audio_bytes("CA_test", 400)
    entry = transcript_store.get("CA_test")
    assert entry["audio_bytes"] == 1200


def test_remove_deletes_entry():
    transcript_store.get_or_create("CA_test")
    transcript_store.remove("CA_test")
    assert transcript_store.get("CA_test") is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd outbound && uv run pytest tests/eval/test_transcript_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval'`

- [ ] **Step 3: Implement transcript store**

Create `outbound/eval/transcript_store.py`:

```python
import threading
from typing import TypedDict


class TranscriptEntry(TypedDict):
    deepgram: list[str]
    azure: list[str]
    audio_bytes: int


_store: dict[str, TranscriptEntry] = {}
_lock = threading.Lock()


def get_or_create(call_sid: str) -> TranscriptEntry:
    with _lock:
        if call_sid not in _store:
            _store[call_sid] = {"deepgram": [], "azure": [], "audio_bytes": 0}
        return _store[call_sid]


def append_deepgram(call_sid: str, utterance: str) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["deepgram"].append(utterance)


def append_azure(call_sid: str, utterance: str) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["azure"].append(utterance)


def add_audio_bytes(call_sid: str, count: int) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["audio_bytes"] += count


def get(call_sid: str) -> TranscriptEntry | None:
    with _lock:
        return _store.get(call_sid)


def remove(call_sid: str) -> None:
    with _lock:
        _store.pop(call_sid, None)
```

- [ ] **Step 4: Run tests**

```bash
cd outbound && uv run pytest tests/eval/test_transcript_store.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add outbound/eval/transcript_store.py outbound/tests/eval/test_transcript_store.py
git commit -m "feat(eval): add thread-safe transcript store"
```

---

## Task 3: Comparator

**Files:**
- Create: `outbound/eval/comparator.py`
- Create: `outbound/tests/eval/test_comparator.py`

- [ ] **Step 1: Write failing tests**

Create `outbound/tests/eval/test_comparator.py`:

```python
import pytest

from eval.comparator import ComparisonResult, compare


def test_identical_transcripts_give_ratio_1():
    result = compare("CA1", ["hello world"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0


def test_completely_different_transcripts_give_low_ratio():
    result = compare("CA1", ["hello world"], ["goodbye moon"], 8000)
    assert result.agreement_ratio < 0.5


def test_segmentation_difference_does_not_penalise():
    # Same words split across turns differently — should still agree fully
    result = compare("CA1", ["hello", "world"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0


def test_audio_duration_from_bytes():
    # 8000 bytes = 1 second of Twilio mulaw (8000 bytes/sec)
    result = compare("CA1", ["test"], ["test"], 8000)
    assert result.audio_duration_seconds == pytest.approx(1.0)


def test_words_per_second():
    # "one two three four" = 4 words over 1 second = 4.0 wps
    result = compare("CA1", ["one two three four"], ["one two three four"], 8000)
    assert result.deepgram_words_per_second == pytest.approx(4.0)
    assert result.azure_words_per_second == pytest.approx(4.0)


def test_word_count_ratio():
    # 4 azure words / 2 deepgram words = 2.0
    result = compare("CA1", ["hi there"], ["hi there you"], 8000)
    assert result.word_count_ratio == pytest.approx(3 / 2)


def test_word_count_ratio_zero_deepgram_returns_zero():
    result = compare("CA1", [], ["hello"], 8000)
    assert result.word_count_ratio == 0.0


def test_deepgram_only_words():
    result = compare("CA1", ["gonna"], ["going to"], 8000)
    assert "gonna" in result.deepgram_only_words


def test_azure_only_words():
    result = compare("CA1", ["gonna"], ["going to"], 8000)
    assert "going" in result.azure_only_words or "to" in result.azure_only_words


def test_turn_counts():
    result = compare("CA1", ["a", "b", "c"], ["x", "y"], 8000)
    assert result.deepgram_turn_count == 3
    assert result.azure_turn_count == 2


def test_zero_audio_bytes_returns_zero_wps():
    result = compare("CA1", ["test"], ["test"], 0)
    assert result.deepgram_words_per_second == 0.0
    assert result.azure_words_per_second == 0.0


def test_punctuation_stripped_in_normalisation():
    result = compare("CA1", ["Hello, world!"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd outbound && uv run pytest tests/eval/test_comparator.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval.comparator'`

- [ ] **Step 3: Implement comparator**

Create `outbound/eval/comparator.py`:

```python
import difflib
import re
import string
from dataclasses import dataclass


@dataclass
class ComparisonResult:
    call_sid: str
    agreement_ratio: float
    word_count_ratio: float
    audio_duration_seconds: float
    deepgram_words_per_second: float
    azure_words_per_second: float
    deepgram_transcript: str
    azure_transcript: str
    deepgram_only_words: list[str]
    azure_only_words: list[str]
    deepgram_turn_count: int
    azure_turn_count: int


def _normalise(utterances: list[str]) -> str:
    text = " ".join(utterances)
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()


def compare(
    call_sid: str,
    deepgram_utterances: list[str],
    azure_utterances: list[str],
    audio_bytes: int,
) -> ComparisonResult:
    deepgram_norm = _normalise(deepgram_utterances)
    azure_norm = _normalise(azure_utterances)

    agreement_ratio = difflib.SequenceMatcher(None, deepgram_norm, azure_norm).ratio()

    deepgram_words = deepgram_norm.split() if deepgram_norm else []
    azure_words = azure_norm.split() if azure_norm else []

    diff = list(difflib.ndiff(deepgram_words, azure_words))
    deepgram_only = [w[2:] for w in diff if w.startswith("- ")]
    azure_only = [w[2:] for w in diff if w.startswith("+ ")]

    audio_duration = audio_bytes / 8000 if audio_bytes > 0 else 0.0
    deepgram_wps = len(deepgram_words) / audio_duration if audio_duration > 0 else 0.0
    azure_wps = len(azure_words) / audio_duration if audio_duration > 0 else 0.0
    word_count_ratio = (
        len(azure_words) / len(deepgram_words) if deepgram_words else 0.0
    )

    return ComparisonResult(
        call_sid=call_sid,
        agreement_ratio=agreement_ratio,
        word_count_ratio=word_count_ratio,
        audio_duration_seconds=audio_duration,
        deepgram_words_per_second=deepgram_wps,
        azure_words_per_second=azure_wps,
        deepgram_transcript=deepgram_norm,
        azure_transcript=azure_norm,
        deepgram_only_words=deepgram_only,
        azure_only_words=azure_only,
        deepgram_turn_count=len(deepgram_utterances),
        azure_turn_count=len(azure_utterances),
    )
```

- [ ] **Step 4: Run tests**

```bash
cd outbound && uv run pytest tests/eval/test_comparator.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add outbound/eval/comparator.py outbound/tests/eval/test_comparator.py
git commit -m "feat(eval): add transcript comparator with normalisation and word diff"
```

---

## Task 4: WebSocket Tee

**Files:**
- Create: `outbound/eval/websocket_tee.py`
- Create: `outbound/tests/eval/test_websocket_tee.py`

- [ ] **Step 1: Write failing tests**

Create `outbound/tests/eval/test_websocket_tee.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd outbound && uv run pytest tests/eval/test_websocket_tee.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval.websocket_tee'`

- [ ] **Step 3: Implement WebSocket tee**

Create `outbound/eval/websocket_tee.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
cd outbound && uv run pytest tests/eval/test_websocket_tee.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add outbound/eval/websocket_tee.py outbound/tests/eval/test_websocket_tee.py
git commit -m "feat(eval): add WebSocketTee to fan out messages to eval queue"
```

---

## Task 5: TranscriptionCapture Processor

**Files:**
- Create: `outbound/eval/transcription_capture.py`

> Note: Pipecat's FrameProcessor is difficult to unit test in isolation without the full pipeline. This task uses a lightweight integration-style test that exercises only the capture logic with minimal Pipecat dependencies.

- [ ] **Step 1: Implement TranscriptionCapture**

Create `outbound/eval/transcription_capture.py`:

```python
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from eval import transcript_store


class TranscriptionCapture(FrameProcessor):
    """Pass-through processor that records Deepgram transcriptions into the eval store.

    Sits between the STT service and the user aggregator. All frames pass through
    unchanged; TranscriptionFrame text is side-copied to transcript_store.
    """

    def __init__(self, call_sid: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._call_sid = call_sid

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text:
            transcript_store.append_deepgram(self._call_sid, frame.text)
        await self.push_frame(frame, direction)
```

- [ ] **Step 2: Verify import works**

```bash
cd outbound && uv run python -c "from eval.transcription_capture import TranscriptionCapture; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add outbound/eval/transcription_capture.py
git commit -m "feat(eval): add TranscriptionCapture pass-through frame processor"
```

---

## Task 6: OTel Emitter

**Files:**
- Create: `outbound/eval/otel_emitter.py`
- Create: `outbound/tests/eval/test_otel_emitter.py`

- [ ] **Step 1: Write failing tests**

Create `outbound/tests/eval/test_otel_emitter.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from eval.comparator import ComparisonResult


def _make_result(**overrides) -> ComparisonResult:
    defaults = dict(
        call_sid="CA123",
        agreement_ratio=0.87,
        word_count_ratio=0.95,
        audio_duration_seconds=12.5,
        deepgram_words_per_second=2.3,
        azure_words_per_second=2.1,
        deepgram_transcript="hello how are you",
        azure_transcript="hello how are you",
        deepgram_only_words=["gonna"],
        azure_only_words=["going", "to"],
        deepgram_turn_count=3,
        azure_turn_count=4,
    )
    defaults.update(overrides)
    return ComparisonResult(**defaults)


def test_emit_calls_gauge_set():
    mock_gauge = MagicMock()
    mock_meter = MagicMock()
    mock_meter.create_gauge.return_value = mock_gauge
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._meter", mock_meter),
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", mock_gauge),
        patch("eval.otel_emitter._wps_gauge", mock_gauge),
        patch("eval.otel_emitter._duration_gauge", mock_gauge),
    ):
        from eval.otel_emitter import emit
        result = _make_result()
        emit(result, to_number="+61400000000")

    assert mock_gauge.set.called


def test_emit_sets_agreement_ratio_value():
    mock_gauge = MagicMock()
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", MagicMock()),
        patch("eval.otel_emitter._wps_gauge", MagicMock()),
        patch("eval.otel_emitter._duration_gauge", MagicMock()),
    ):
        from eval import otel_emitter
        result = _make_result(agreement_ratio=0.87)
        otel_emitter.emit(result, to_number="+61400000000")

    # agreement_gauge.set is called with (value, attrs) — check value is 0.87
    set_call_args = [call.args[0] for call in mock_gauge.set.call_args_list]
    assert 0.87 in set_call_args


def test_emit_span_sets_call_sid():
    mock_gauge = MagicMock()
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", mock_gauge),
        patch("eval.otel_emitter._wps_gauge", mock_gauge),
        patch("eval.otel_emitter._duration_gauge", mock_gauge),
    ):
        from eval import otel_emitter
        result = _make_result(call_sid="CA999")
        otel_emitter.emit(result, to_number="+61400000000")

    call_args_list = mock_span.set_attribute.call_args_list
    attr_names = [args[0][0] for args in call_args_list]
    assert "call_sid" in attr_names
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd outbound && uv run pytest tests/eval/test_otel_emitter.py -v
```

Expected: `ModuleNotFoundError: No module named 'eval.otel_emitter'`

- [ ] **Step 3: Implement OTel emitter**

Create `outbound/eval/otel_emitter.py`:

```python
from opentelemetry import metrics, trace

from eval.comparator import ComparisonResult

_meter = metrics.get_meter("stt.eval")
_tracer = trace.get_tracer("stt.eval")

_agreement_gauge = _meter.create_gauge("stt.eval.agreement_ratio")
_word_ratio_gauge = _meter.create_gauge("stt.eval.word_count_ratio")
_wps_gauge = _meter.create_gauge("stt.eval.words_per_second")
_duration_gauge = _meter.create_gauge("stt.eval.audio_duration_seconds")

# OTel span attribute values are limited to 1024 chars by default in most SDKs
_MAX_TRANSCRIPT_CHARS = 1000


def emit(result: ComparisonResult, to_number: str = "") -> None:
    attrs = {"call_sid": result.call_sid, "to_number": to_number}

    _agreement_gauge.set(result.agreement_ratio, attrs)
    _word_ratio_gauge.set(result.word_count_ratio, attrs)
    _wps_gauge.set(result.deepgram_words_per_second, {**attrs, "stt": "deepgram"})
    _wps_gauge.set(result.azure_words_per_second, {**attrs, "stt": "azure"})
    _duration_gauge.set(result.audio_duration_seconds, attrs)

    with _tracer.start_as_current_span("stt_comparison") as span:
        span.set_attribute("call_sid", result.call_sid)
        span.set_attribute("to_number", to_number)
        span.set_attribute("agreement_ratio", result.agreement_ratio)
        span.set_attribute("word_count_ratio", result.word_count_ratio)
        span.set_attribute("audio_duration_seconds", result.audio_duration_seconds)
        span.set_attribute("deepgram_words_per_second", result.deepgram_words_per_second)
        span.set_attribute("azure_words_per_second", result.azure_words_per_second)
        span.set_attribute(
            "deepgram_transcript", result.deepgram_transcript[:_MAX_TRANSCRIPT_CHARS]
        )
        span.set_attribute(
            "azure_transcript", result.azure_transcript[:_MAX_TRANSCRIPT_CHARS]
        )
        span.set_attribute("deepgram_only_words", str(result.deepgram_only_words))
        span.set_attribute("azure_only_words", str(result.azure_only_words))
        span.set_attribute("deepgram_turn_count", result.deepgram_turn_count)
        span.set_attribute("azure_turn_count", result.azure_turn_count)
```

- [ ] **Step 4: Run tests**

```bash
cd outbound && uv run pytest tests/eval/test_otel_emitter.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add outbound/eval/otel_emitter.py outbound/tests/eval/test_otel_emitter.py
git commit -m "feat(eval): add OTel emitter for gauges and stt_comparison span"
```

---

## Task 7: Azure STT Handler

**Files:**
- Create: `outbound/eval/azure_stt_handler.py`

> This component coordinates the Azure Speech SDK (callback-based, runs in SDK threads) with asyncio. The callbacks write to `transcript_store` using the threading lock already in place. A 3-second sleep after the sentinel gives the Deepgram pipeline time to flush its last frames before comparison runs.

- [ ] **Step 1: Implement Azure STT handler**

Create `outbound/eval/azure_stt_handler.py`:

```python
import asyncio
import audioop
import base64
import json
import os

import azure.cognitiveservices.speech as speechsdk
from loguru import logger

from eval import comparator, otel_emitter, transcript_store


async def run_azure_stt_from_queue(queue: asyncio.Queue) -> None:
    """Read Twilio media messages from queue, stream to Azure STT, compare at call end."""
    call_sid: str | None = None
    to_number: str = ""

    speech_config = speechsdk.SpeechConfig(
        subscription=os.environ["AZURE_SPEECH_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
    )
    # Twilio mulaw is decoded to 16-bit PCM at 8kHz before pushing to Azure
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=8000,
        bits_per_sample=16,
        channels=1,
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if (
            evt.result.reason == speechsdk.ResultReason.RecognizedSpeech
            and call_sid
            and evt.result.text.strip()
        ):
            transcript_store.append_azure(call_sid, evt.result.text.strip())

    recognizer.recognized.connect(on_recognized)
    recognizer.start_continuous_recognition()

    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break  # sentinel: WebSocket closed

            if msg.get("type") != "websocket.receive":
                continue

            text = msg.get("text", "")
            if not text:
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            event = data.get("event")

            if event == "start":
                call_sid = data["start"]["callSid"]
                params = data["start"].get("customParameters", {})
                to_number = params.get("to_number", "")
                transcript_store.get_or_create(call_sid)
                logger.info(f"Azure STT eval started for call {call_sid}")

            elif event == "media" and call_sid:
                raw = base64.b64decode(data["media"]["payload"])
                # G.711 mulaw → 16-bit linear PCM (audioop available in Python ≤ 3.12)
                pcm = audioop.ulaw2lin(raw, 2)
                push_stream.write(pcm)
                transcript_store.add_audio_bytes(call_sid, len(raw))

    except Exception as e:
        logger.error(f"Azure STT handler error: {e}")

    finally:
        push_stream.close()
        # Wait 3 s to let the Deepgram pipeline flush its last frames before comparing
        await asyncio.sleep(3.0)
        recognizer.stop_continuous_recognition()

        if call_sid:
            entry = transcript_store.get(call_sid)
            if entry:
                result = comparator.compare(
                    call_sid=call_sid,
                    deepgram_utterances=list(entry["deepgram"]),
                    azure_utterances=list(entry["azure"]),
                    audio_bytes=entry["audio_bytes"],
                )
                logger.info(
                    f"STT eval complete — call={call_sid} "
                    f"agreement={result.agreement_ratio:.2f} "
                    f"word_ratio={result.word_count_ratio:.2f}"
                )
                otel_emitter.emit(result, to_number=to_number)
                transcript_store.remove(call_sid)
```

- [ ] **Step 2: Verify import**

```bash
cd outbound && uv run python -c "from eval.azure_stt_handler import run_azure_stt_from_queue; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add outbound/eval/azure_stt_handler.py
git commit -m "feat(eval): add Azure STT queue handler with mulaw→PCM conversion"
```

---

## Task 8: Wire Up `/ws` Endpoint (server.py + modal_app.py)

**Files:**
- Modify: `outbound/server.py`
- Modify: `outbound/modal_app.py`

- [ ] **Step 1: Update server.py**

Replace the entire `/ws` endpoint in `outbound/server.py`. Also add `import asyncio` at the top of the file.

Add `import asyncio` after the existing `import os` on line 13:

```python
import asyncio
```

Replace the `/ws` endpoint (lines 83–105) with:

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connection from Twilio Media Streams.

    Wraps the websocket with a message tee so the Azure STT eval handler
    receives the same Twilio audio frames without touching the Pipecat pipeline.
    """
    from bot import bot
    from eval.azure_stt_handler import run_azure_stt_from_queue
    from eval.websocket_tee import WebSocketTee
    from pipecat.runner.types import WebSocketRunnerArguments

    await websocket.accept()
    logger.info("WebSocket connection accepted for outbound call")

    queue: asyncio.Queue = asyncio.Queue()
    tee = WebSocketTee(websocket, queue)

    results = await asyncio.gather(
        bot(WebSocketRunnerArguments(websocket=tee)),
        run_azure_stt_from_queue(queue),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Error in WebSocket task: {r}")
```

- [ ] **Step 2: Update modal_app.py image definition**

In `outbound/modal_app.py`, update the image definition to include the `eval/` directory. Replace lines 20–32 (the `image = ...` block) with:

```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libopenblas-dev")
    .pip_install(
        "pipecat-ai[websocket,groq,silero,deepgram,rnnoise,runner,local-smart-turn-v3]>=0.0.99",
        "pipecatcloud>=0.2.18",
        "twilio",
        "python-dotenv",
        "requests",
        "azure-cognitiveservices-speech>=1.38.0",
        "opentelemetry-api>=1.20.0",
        "opentelemetry-sdk>=1.20.0",
    )
    .run_commands("python -c 'from pyrnnoise import RNNoise; RNNoise(sample_rate=48000)'")
    .add_local_file("bot.py", "/root/bot.py")
    .add_local_dir("eval", "/root/eval")
)
```

- [ ] **Step 3: Update modal_app.py `/ws` endpoint**

Inside the `serve()` function in `modal_app.py`, add these imports alongside the existing eager imports (after line 67 `from pipecat.runner.types import WebSocketRunnerArguments`):

```python
    import asyncio
    from eval.azure_stt_handler import run_azure_stt_from_queue
    from eval.websocket_tee import WebSocketTee
```

Replace the `websocket_endpoint` function (lines 132–143) with:

```python
    @web_app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """Accept Twilio Media Stream, run Pipecat bot and Azure STT eval in parallel."""
        await websocket.accept()
        logger.info("WebSocket connection accepted for outbound call")

        queue: asyncio.Queue = asyncio.Queue()
        tee = WebSocketTee(websocket, queue)

        results = await asyncio.gather(
            bot(WebSocketRunnerArguments(websocket=tee)),
            run_azure_stt_from_queue(queue),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Error in WebSocket task: {type(r).__name__}: {r}")
                logger.error(traceback.format_exc())
```

- [ ] **Step 4: Verify server.py starts cleanly**

```bash
cd outbound && uv run python -c "from server import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add outbound/server.py outbound/modal_app.py
git commit -m "feat(eval): wire WebSocketTee and Azure STT handler into /ws endpoint"
```

---

## Task 9: Wire TranscriptionCapture into Pipeline

**Files:**
- Modify: `outbound/bot.py` (lines 123–133 — the `Pipeline([...])` block and `run_bot` signature)

- [ ] **Step 1: Add import and update pipeline in bot.py**

Add the import at the top of `outbound/bot.py`, after the existing pipecat imports (after line 40):

```python
from eval.transcription_capture import TranscriptionCapture
```

Update `run_bot` signature (line 83) to remove the default for `call_sid` (make it explicit):

```python
async def run_bot(transport: BaseTransport, handle_sigint: bool, call_sid: str = ""):
```

Inside `run_bot`, replace the `Pipeline([...])` block (lines 123–133) with:

```python
    capture = TranscriptionCapture(call_sid=call_sid)

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text
            capture,  # Eval: capture Deepgram transcriptions into eval store
            user_aggregator,
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to client
            assistant_aggregator,
        ]
    )
```

- [ ] **Step 2: Verify bot.py imports cleanly**

```bash
cd outbound && uv run python -c "from bot import run_bot; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run all tests to confirm nothing broken**

```bash
cd outbound && uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add outbound/bot.py
git commit -m "feat(eval): insert TranscriptionCapture into Pipecat pipeline"
```

---

## Task 10: Full Test Run and Manual Smoke Test Checklist

- [ ] **Step 1: Run full test suite**

```bash
cd outbound && uv run pytest tests/ -v --tb=short
```

Expected: all tests pass, no import errors.

- [ ] **Step 2: Validate Docker build (optional, if Docker is available)**

```bash
cd outbound && docker compose build
```

Expected: build succeeds with new dependencies.

- [ ] **Step 3: Manual smoke test checklist**

Before a real call, verify the following env vars are set in `.env`:
```
AZURE_SPEECH_KEY=<your key>
AZURE_SPEECH_REGION=<your region, e.g. australiaeast>
```

Make a test call via `POST /dialout`. After the call ends, check logs for:
```
Azure STT eval started for call CA...
STT eval complete — call=CA... agreement=0.XX word_ratio=0.XX
```

If OTel is wired: confirm `stt.eval.agreement_ratio` gauge and `stt_comparison` span appear in Datadog.

- [ ] **Step 4: Final commit if any fixups were needed**

```bash
git add -p  # stage only intentional changes
git commit -m "fix(eval): address smoke test findings"
```

---

## Environment Variables Required

Add to `.env` and Modal secrets:

| Variable | Description |
|---|---|
| `AZURE_SPEECH_KEY` | Azure Cognitive Services subscription key |
| `AZURE_SPEECH_REGION` | Azure region (e.g. `australiaeast`, `eastus`) |

OTel exporter config (when wiring up OTel):

| Variable | Description |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Your Datadog OTel collector endpoint |
| `OTEL_SERVICE_NAME` | e.g. `twilio-outbound-bot` |
