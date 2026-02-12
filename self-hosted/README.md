# Twilio Chatbot: Self-Hosted

A Pipecat-based voice bot that replaces API services (Deepgram, Groq) with self-hosted open-source models on Modal GPUs. Uses the same latency observers from the [latency bot](../latency/) for an apples-to-apples comparison.

## Architecture

Three separate Modal containers, each with its own GPU:

```
Phone Call <-> Twilio <-> Media Streams (WebSocket) <-> Bot Container (T4 GPU)
                                                          ├── Faster-Whisper STT (in-process)
                                                          ├── Silero VAD + Smart Turn v3
                                                          ├── Latency observers
                                                          │
                                                          ├──HTTP──> vLLM Container (A10G GPU)
                                                          │          Llama 3.1 8B Instruct
                                                          │
                                                          └──HTTP──> XTTS Container (T4 GPU)
                                                                     XTTS v2 streaming
```

### AI Services

| Service | Provider | Model | Deployment |
|---------|----------|-------|------------|
| STT | Faster-Whisper | large-v3-turbo | In-process (bot container GPU) |
| LLM | vLLM | meta-llama/Meta-Llama-3.1-8B-Instruct | Separate Modal container |
| TTS | XTTS v2 | xtts_v2 (Coqui) | Separate Modal container |
| VAD | Silero | + Smart Turn v3 | In-process (same as inbound/latency bots) |

### What's Different from the API-Based Bots

| | Inbound / Latency bot | Self-hosted bot |
|-|----------------------|-----------------|
| **STT** | Deepgram nova-3 (streaming, API) | Faster-Whisper large-v3-turbo (batch, in-process) |
| **LLM** | Groq llama-3.3-70b (API) | vLLM Llama 3.1 8B (self-hosted) |
| **TTS** | Deepgram aura-2-theia-en (API) | XTTS v2 (self-hosted) |
| **Bot GPU** | None | T4 (for Whisper inference) |
| **API keys needed** | Deepgram, Groq | None (self-hosted URLs instead) |
| **STT mode** | Streaming (partial transcriptions during speech) | Batch (full transcription after speech ends) |

### Latency Observers

Same three observers as the [latency bot](../latency/), producing identical output for direct comparison:

- **`MetricsLogObserver`** — Per-service TTFB, token usage, TTS character counts
- **`UserBotLatencyLogObserver`** — End-to-end latency per turn + summary
- **`LatencyBreakdownObserver`** — Per-turn summary table at call end

## Prerequisites

- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- A [HuggingFace](https://huggingface.co/) account with access to [meta-llama/Meta-Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct) (gated model)
- [Modal](https://modal.com/) CLI installed and authenticated

## Setup

1. Install the Modal CLI:

   ```sh
   uv tool install modal
   modal setup
   ```

2. Create an `.env` file:

   ```sh
   cd self-hosted
   cp env.example .env
   ```

   Fill in the values:

   ```
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   HF_TOKEN=your_huggingface_token
   ```

## Deployment

Deploy the three containers in order — the backend services first, then the bot.

### 1. Deploy vLLM (LLM server)

```sh
cd self-hosted
modal deploy modal_vllm.py
```

Note the URL from the output (e.g. `https://<workspace>--self-hosted-vllm-serve.modal.run`). Add `/v1` and set it in your `.env`:

```
VLLM_BASE_URL=https://<workspace>--self-hosted-vllm-serve.modal.run/v1
```

Verify it's running:

```sh
curl https://<workspace>--self-hosted-vllm-serve.modal.run/v1/models
```

### 2. Deploy XTTS (TTS server)

```sh
cd self-hosted
modal deploy modal_xtts.py
```

Note the URL and set it in your `.env`:

```
XTTS_BASE_URL=https://<workspace>--self-hosted-xtts-serve.modal.run
```

Verify it's running:

```sh
curl https://<workspace>--self-hosted-xtts-serve.modal.run/studio_speakers
```

### 3. Deploy the bot

```sh
cd self-hosted
modal serve modal_app.py    # dev mode (temporary URL, live reload)
modal deploy modal_app.py   # production (permanent URL)
```

### 4. Configure Twilio

Set your Twilio phone number's incoming call webhook to the bot's Modal URL (POST method), the same way as the [inbound bot](../inbound/README.md#configure-twilio).

## Project Structure

```
self-hosted/
  bot.py              # Bot logic: Whisper STT + vLLM LLM + XTTS TTS + observers
  modal_app.py        # Modal deployment: bot container (T4 GPU)
  modal_vllm.py       # Modal deployment: vLLM server (A10G GPU)
  modal_xtts.py       # Modal deployment: XTTS server (T4 GPU)
  tts_server.py       # FastAPI server implementing XTTS streaming API
  observers.py        # Custom LatencyBreakdownObserver (copied from latency/)
  pyproject.toml      # Python project config and dependencies
  Dockerfile          # Docker alternative
  env.example         # Template for .env
```
