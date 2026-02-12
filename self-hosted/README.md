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

**For Modal deployment:**
- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- A [HuggingFace](https://huggingface.co/) account with access to [meta-llama/Meta-Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct) (gated model)
- [Modal](https://modal.com/) CLI installed and authenticated

**For Docker (local) deployment:**
- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- [Docker](https://docs.docker.com/get-docker/) (and Docker Compose)
- [VS Code](https://code.visualstudio.com/) with a GitHub or Microsoft account (for dev tunnels)

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

## Docker Deployment (Local)

Run the entire stack locally with Docker Compose. All services run on CPU — no GPU required. This works on Mac (Apple Silicon or Intel) and Linux.

### 1. Create `.env`

```sh
cd self-hosted
cp env.example .env
```

Fill in Twilio credentials only (no HuggingFace token needed — Ollama downloads models directly):

```
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
```

### 2. Forward port 7860 with VS Code dev tunnels

Twilio needs a public URL to send media streams. See the [inbound bot README](../inbound/README.md#docker-deployment) for full dev tunnel setup details.

### 3. Start the stack

```sh
cd self-hosted
export PROXY_HOST=<tunnel-name>-7860-<region>.devtunnels.ms
docker compose up --build
```

This starts three containers:
- **ollama** — LLM server (Llama 3.1 8B)
- **xtts** — TTS server (XTTS v2, running on CPU)
- **bot** — Pipecat bot with Whisper STT (running on CPU)

### 4. Pull the LLM model (first time only)

In a separate terminal, pull the Llama model into the Ollama container:

```sh
docker exec self-hosted-ollama-1 ollama pull llama3.1:8b
```

The model is cached in a Docker volume (`ollama_data`), so this only needs to happen once.

### 5. Configure Twilio

Point your Twilio phone number's incoming call webhook to your dev tunnel URL (POST method).

### Performance on CPU

All services run on CPU in Docker, which is slower than GPU but functional:

| Service | CPU performance |
|---------|----------------|
| **Whisper STT** | Slower than GPU but usable. Uses `int8` compute type for speed. |
| **Ollama LLM** | Runs well on CPU. On Mac, running Ollama natively (outside Docker) gives Metal GPU acceleration. |
| **XTTS TTS** | Noticeably slower than GPU. Expect higher TTS TTFB. |

For NVIDIA GPU acceleration on Linux, uncomment the `deploy` sections in `docker-compose.yml`.

## Native Mac Deployment (Apple Silicon)

Run all services natively on macOS to take advantage of Apple Silicon GPU acceleration. This gives significantly better performance than Docker (which runs on CPU only).

### Prerequisites

- [Ollama](https://ollama.com/) installed natively
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- Python 3.12+

### 1. Create `.env`

```sh
cd self-hosted
cp env.example .env
```

Fill in Twilio credentials and set Mac-specific device options:

```
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
WHISPER_DEVICE=mlx
TTS_DEVICE=mps
LLM_MODEL=llama3.1:8b
```

### 2. Start Ollama and pull the model

```sh
ollama pull llama3.1:8b
ollama serve   # if not already running as a service
```

### 3. Start the XTTS server

```sh
cd self-hosted
TTS_DEVICE=mps uv run uvicorn tts_server:app --host 0.0.0.0 --port 8001
```

This uses the Metal Performance Shaders (MPS) backend for GPU-accelerated TTS inference.

### 4. Forward port 7860 with VS Code dev tunnels

Same as the Docker deployment — Twilio needs a public URL. See the [inbound bot README](../inbound/README.md#docker-deployment) for setup details.

### 5. Start the bot

In a separate terminal:

```sh
cd self-hosted
export WHISPER_DEVICE=mlx
export XTTS_BASE_URL=http://localhost:8001
export VLLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=llama3.1:8b
uv run python -m pipecat.runner.run --transport twilio --host 0.0.0.0 --proxy <your-dev-tunnel-host>
```

### 6. Configure Twilio

Point your Twilio phone number's incoming call webhook to your dev tunnel URL (POST method).

### Performance on Apple Silicon

| Service | Mac GPU acceleration |
|---------|---------------------|
| **Whisper STT** | Uses MLX framework (`WhisperSTTServiceMLX`). Significantly faster than CPU. |
| **Ollama LLM** | Native Ollama uses Metal GPU automatically. No extra config needed. |
| **XTTS TTS** | Uses PyTorch MPS backend (`TTS_DEVICE=mps`). Much faster than CPU. |

## Project Structure

```
self-hosted/
  bot.py              # Bot logic: Whisper STT + vLLM/Ollama LLM + XTTS TTS + observers
  modal_app.py        # Modal deployment: bot container (T4 GPU)
  modal_vllm.py       # Modal deployment: vLLM server (A10G GPU)
  modal_xtts.py       # Modal deployment: XTTS server (T4 GPU)
  tts_server.py       # FastAPI server implementing XTTS streaming API
  observers.py        # Custom LatencyBreakdownObserver (copied from latency/)
  pyproject.toml      # Python project config and dependencies
  Dockerfile          # Docker image for the bot
  Dockerfile.xtts     # Docker image for the XTTS server
  docker-compose.yml  # Local deployment: bot + Ollama + XTTS
  env.example         # Template for .env
```
