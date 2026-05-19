# Dual STT Eval — Design Spec

**Date:** 2026-05-18
**Branch:** feat/dual-stt-eval
**Scope:** `outbound/` folder only

---

## Problem

The outbound bot uses Deepgram nova-3 as its STT. There is no way to gauge transcription confidence or detect failure modes (accents, phone audio quality, domain vocabulary) without a reference point. We want to run a shadow STT (Azure Cognitive Services) in parallel to build a call-by-call confidence signal, without adding any latency or burden to the live Pipecat pipeline.

---

## Preconditions

- OpenTelemetry SDK is configured in the outbound project (MeterProvider + TracerProvider exporting via OTLP to a Datadog OTel collector).
- Azure Cognitive Services STT credentials (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`) are available as environment variables.
- The outbound bot is deployed to Modal or Docker with the existing setup.

---

## Approach: Dual Twilio Media Stream

Twilio natively supports multiple `<Stream>` elements inside a single `<Connect>` TwiML verb. Each stream receives an identical fork of the call audio. This means:

- The Pipecat pipeline's core services (Deepgram STT, Groq LLM, Deepgram TTS) are unchanged — no latency or processing burden is added to the hot path.
- A new lightweight `/ws-eval` endpoint receives the same audio independently and runs Azure STT only.
- A tiny `TranscriptionCapture` pass-through processor is inserted between the Deepgram STT and the user aggregator to capture transcriptions into a shared store. It does nothing except observe `TranscriptionFrame` objects — all frames pass through unchanged.

---

## Architecture

```
Twilio call audio
      │
      ├── Stream 1 ──► /ws        ──► Pipecat pipeline (unchanged)
      │                                      │
      │                                 TranscriptionCapture (pass-through)
      │                                      │
      │                               transcript_store[call_sid].deepgram
      │
      └── Stream 2 ──► /ws-eval   ──► AzureSTTHandler (standalone)
                                              │
                                        transcript_store[call_sid].azure
                                              │
                                   (on disconnect) Comparator
                                              │
                                         OtelEmitter
                                              │
                              OTel Collector ──► Datadog
```

---

## Components

### 1. TwiML change — `outbound/server_utils.py`

Update `generate_twiml()` to add a second `<Stream>` element pointing to `/ws-eval`:

```xml
<Response>
  <Connect>
    <Stream url="wss://.../ws">
      <Parameter name="to_number" value="..."/>
      <Parameter name="from_number" value="..."/>
    </Stream>
    <Stream url="wss://.../ws-eval">
      <Parameter name="to_number" value="..."/>
      <Parameter name="from_number" value="..."/>
    </Stream>
  </Connect>
  <Pause length="20"/>
</Response>
```

The eval stream URL is derived from the same base URL as the main stream, with `/ws-eval` as the path.

---

### 2. Transcript store — `outbound/eval/transcript_store.py`

A module-level in-memory dict keyed by `call_sid`. Both the Pipecat pipeline (via `TranscriptionCapture`) and the `/ws-eval` handler write to it.

```python
TranscriptEntry = TypedDict("TranscriptEntry", {
    "deepgram": list[str],
    "azure": list[str],
    "audio_bytes": int,  # written by /ws-eval, used for duration calc
})

store: dict[str, TranscriptEntry] = {}
```

Entries are created on first write and removed after the comparison is emitted. No persistence — this is ephemeral per-call state.

---

### 3. TranscriptionCapture processor — `outbound/eval/transcription_capture.py`

A minimal Pipecat `FrameProcessor` inserted between the Deepgram STT and the `UserContextAggregator` in `bot.py`. It passes all frames through unchanged, but intercepts `TranscriptionFrame` objects to append the text to `transcript_store[call_sid].deepgram`.

Pipeline change in `bot.py`:
```
transport.input → stt → TranscriptionCapture → user_aggregator → llm → tts → transport.output
```

The `call_sid` is passed to `TranscriptionCapture` at construction time (already available in `bot()`).

---

### 4. Azure STT handler — `outbound/eval/azure_stt_handler.py`

A standalone async function `run_azure_stt(websocket, call_sid)` that:

1. Accepts the Twilio Media Stream WebSocket connection.
2. Decodes the base64 mulaw audio payload from each Twilio media message.
3. Converts mulaw to PCM (Azure STT requires PCM, 16kHz or 8kHz).
4. Streams PCM to Azure Cognitive Services STT via the `azure-cognitiveservices-speech` SDK.
5. Appends each recognised utterance to `transcript_store[call_sid].azure`.
6. Counts total audio bytes received → stored as `transcript_store[call_sid].audio_bytes`.
7. On WebSocket disconnect (call end), triggers the comparator and emitter.

---

### 5. `/ws-eval` endpoint — `outbound/server.py` + `outbound/modal_app.py`

A new WebSocket endpoint added to both deployment servers:

```python
@app.websocket("/ws-eval")
async def websocket_eval_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_sid = extract_call_sid_from_stream(websocket)
    await run_azure_stt(websocket, call_sid)
```

`extract_call_sid_from_stream` parses the `start` event Twilio sends at stream open (contains `callSid` in the JSON payload).

---

### 6. Comparator — `outbound/eval/comparator.py`

Takes two lists of utterance strings and an audio byte count. Returns a `ComparisonResult` dataclass.

**Algorithm:**
1. Concatenate each STT's utterances into a single string.
2. Normalise both: lowercase, strip punctuation, collapse whitespace.
3. `agreement_ratio` = `difflib.SequenceMatcher(a=deepgram_norm, b=azure_norm).ratio()`
4. Word-level diff: `difflib.ndiff(deepgram_words, azure_words)` → split into `deepgram_only` and `azure_only` word lists.
5. `audio_duration_seconds` = `audio_bytes / 8000` (Twilio mulaw is 8000 bytes/sec).
6. `deepgram_wps` = `len(deepgram_words) / audio_duration_seconds`
7. `azure_wps` = `len(azure_words) / audio_duration_seconds`
8. `word_count_ratio` = `azure_word_count / deepgram_word_count` (1.0 = identical verbosity)

```python
@dataclass
class ComparisonResult:
    call_sid: str
    agreement_ratio: float        # 0.0–1.0, higher = more agreement
    word_count_ratio: float       # azure/deepgram word count ratio, 1.0 = identical
    audio_duration_seconds: float
    deepgram_words_per_second: float
    azure_words_per_second: float
    deepgram_transcript: str      # full normalised string
    azure_transcript: str         # full normalised string
    deepgram_only_words: list[str]
    azure_only_words: list[str]
    deepgram_turn_count: int
    azure_turn_count: int
```

---

### 7. OTel emitter — `outbound/eval/otel_emitter.py`

Emits two things per call:

**A. OTel Metrics (gauges) — for Datadog dashboards and trend analysis**

| Metric name | Value | Tags |
|---|---|---|
| `stt.eval.agreement_ratio` | 0.0–1.0 | `call_sid`, `to_number` |
| `stt.eval.word_count_ratio` | float | `call_sid`, `to_number` |
| `stt.eval.words_per_second` | float | `call_sid`, `stt:deepgram` |
| `stt.eval.words_per_second` | float | `call_sid`, `stt:azure` |
| `stt.eval.audio_duration_seconds` | float | `call_sid` |

**B. OTel Span — for per-call drill-down in Datadog APM**

One span named `stt_comparison` per call, with all `ComparisonResult` fields as span attributes. This enables Datadog APM queries like:
- `resource_name:stt_comparison @agreement_ratio:<0.7` → surface low-confidence calls
- Open any span to read both full transcripts side by side

---

## Eval Workflow (Post-deployment)

1. **Dashboard:** Plot `stt.eval.agreement_ratio` over time. Any regression (drop in average) indicates a change in STT behaviour.
2. **Log/Trace explorer:** Filter `resource_name:stt_comparison @agreement_ratio:<0.7` to review calls where the two STTs significantly diverged.
3. **Word diff review:** `deepgram_only_words` and `azure_only_words` on a span reveal the specific words each STT uniquely detected — useful for spotting domain vocabulary gaps.
4. **Density check:** `word_count_ratio` far from 1.0 (e.g., < 0.7 or > 1.3) on a call with high `audio_duration_seconds` suggests one STT is significantly under- or over-transcribing.

---

## New Files

```
outbound/
  eval/
    __init__.py
    transcript_store.py
    transcription_capture.py
    azure_stt_handler.py
    comparator.py
    otel_emitter.py
```

## Changed Files

```
outbound/
  server_utils.py     — generate_twiml() adds second <Stream>
  server.py           — add /ws-eval WebSocket endpoint
  modal_app.py        — add /ws-eval WebSocket endpoint
  bot.py              — insert TranscriptionCapture into pipeline
  pyproject.toml      — add azure-cognitiveservices-speech, opentelemetry-sdk deps
```

---

## Out of Scope

- Ground truth transcripts / WER computation
- Real-time per-utterance comparison (comparison happens at call end only)
- Storing transcripts beyond the call lifetime (no database, no file persistence)
- Modifying the inbound bot
