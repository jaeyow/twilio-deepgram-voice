# Twilio Inbound Bot — Azure STT

Inbound voice bot using Azure Cognitive Services for speech-to-text, Groq for LLM, and Deepgram for text-to-speech. Built on the [Pipecat](https://github.com/pipecat-ai/pipecat) framework and deployed to [Modal](https://modal.com).

## Pipeline Overview

```
Twilio (caller) ──► WebSocket ──► RNNoise filter ──► Silero VAD
                                                          │
                                              ┌───────────┘
                                              ▼
                                        Azure STT
                                              │
                                              ▼
                                       user_aggregator  ◄── Smart Turn v3
                                              │
                                              ▼
                                         Groq LLM
                                              │
                                              ▼
                                       Deepgram TTS
                                              │
                                              ▼
                                 WebSocket ──► Twilio (caller)
```

## How the Components Relate

### Silero VAD (Voice Activity Detection)

VAD runs **inside the transport**, before any other stage in the pipeline. It continuously analyses incoming audio frames from Twilio and makes a binary decision: is the caller speaking, or is this silence?

- `start_secs=0.2` — 200ms of detected speech must pass before the caller is considered to have started talking. This prevents noise bursts from being treated as speech.
- `stop_secs=0.5` — 500ms of silence must pass before the caller is considered to have stopped talking.

VAD gates the audio stream. When it detects speech it emits a `UserStartedSpeakingFrame` and begins forwarding audio frames downstream. When it detects silence it emits a `UserStoppedSpeakingFrame`. Audio outside of a speech segment is dropped — Azure STT never sees it.

### Azure STT (Speech-to-Text)

Azure STT receives the VAD-gated audio frames and converts them to text. Under the hood it uses Azure Cognitive Services Speech SDK's **continuous recognition** mode over a `PushAudioInputStream` — audio bytes are pushed in as they arrive, and Azure sends back recognition results asynchronously via callbacks.

Two types of results come back:

- **Interim (`recognizing`)** — partial transcripts emitted while the caller is still speaking. These are forwarded downstream as `InterimTranscriptionFrame`. They give the pipeline an early signal that speech is happening.
- **Final (`recognized`)** — a complete utterance once Azure is confident the phrase is finished. Forwarded as `TranscriptionFrame`.

Azure STT is a **cloud streaming service** — audio is sent over a persistent WebSocket to Azure's servers and transcripts come back in near real-time. This means latency depends on network conditions to the Azure region you configure (`AZURE_SPEECH_REGION`). If that connection degrades or times out, Azure sends a cancellation event and the session is dead — no transcripts will arrive until a new session is established. This is the key failure mode to be aware of in production.

### Smart Turn v3 (Turn Detection)

VAD detecting silence does not mean the caller has finished their turn. A natural pause mid-sentence, or a brief "um", would trigger `UserStoppedSpeakingFrame` even though the caller intends to keep going. Smart Turn solves this.

`LocalSmartTurnAnalyzerV3` runs a small local model that receives the final transcript from Azure STT plus the VAD timing signals and decides: **is this utterance semantically complete?**

- `stop_secs=1.5` — Smart Turn waits up to 1.5 seconds of silence before it will declare the turn over. If it becomes confident the utterance is complete before 1.5s it fires early; if not, 1.5s is the hard cutoff.
- `pre_speech_ms=0.0` — no pre-speech audio buffering.

Only when Smart Turn signals completion does `user_aggregator` release the accumulated transcript and fire the LLM. This prevents the bot from interrupting the caller mid-thought.

The relationship between VAD and Smart Turn is:

```
VAD says "speech stopped"  ──►  Smart Turn analyses transcript
                                        │
                          ┌─────────────┴──────────────┐
                          │                            │
                   semantically done?           not done yet
                          │                            │
                   release to LLM              wait for more speech
```

### Groq LLM

Once `user_aggregator` receives a complete turn it builds the full conversation context (system prompt + history + new user message) and sends it to the Groq API. Groq runs `llama-3.1-8b-instant` (or whichever model you configure) and streams tokens back. The streamed text is forwarded to the TTS stage as it arrives so TTS can begin speaking before the LLM has finished generating — this is what keeps latency low.

### Deepgram TTS (Text-to-Speech)

Deepgram TTS receives the streamed LLM tokens and converts them to audio using the `aura-2-theia-en` voice. Like Azure STT, it is a cloud streaming service — text is sent over a WebSocket to Deepgram and audio frames come back in real-time. Audio is written to the Twilio WebSocket as it arrives so the caller hears the bot speaking with minimal delay.

## End-to-End Latency Breakdown

```
caller stops speaking
        │
  VAD silence (500ms minimum)
        │
  Smart Turn decision (up to 1.5s)
        │
  Azure STT final transcript (already in-flight during speech)
        │
  Groq LLM first token (~70–100ms on Groq)
        │
  Deepgram TTS first audio chunk (~35ms)
        │
caller hears bot response
```

The dominant latency contributor in this stack is the combined VAD silence window + Smart Turn window (up to 2s total worst case). Azure STT itself adds minimal latency because it transcribes while the caller speaks — by the time Smart Turn fires, the final transcript is usually already available.

## Setup

### Environment Variables

Copy `env.example` to `.env` and fill in your credentials:

```
GROQ_API_KEY=
DEEPGRAM_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=eastus
# Optional: only if using a custom Azure Speech model
AZURE_SPEECH_ENDPOINT_ID=
```

### Local Development

```bash
uv venv && uv sync
uv run python bot.py
```

### Deploy to Modal

```bash
# Dev mode — temporary URL, live reload on save
modal serve modal_app.py

# Production — permanent URL
modal deploy modal_app.py
```

Set your Twilio phone number's incoming call webhook to the Modal URL (POST method).
