# Twilio Chatbot: Latency Instrumentation

A Pipecat-based voice bot with built-in latency tracking using Pipecat's observer system. This is a self-contained version of the [inbound bot](../inbound/) with three observers wired up to measure per-component and end-to-end voice-to-voice latency.

Blog post: [Where Does the Time Go? Measuring Voice-to-Voice Latency with Pipecat](../blog-latency.md)

## What's Different from the Inbound Bot

This bot adds three observers to the pipeline (in `bot.py`):

```python
task = PipelineTask(
    pipeline,
    params=PipelineParams(..., enable_metrics=True, enable_usage_metrics=True),
    observers=[
        MetricsLogObserver(),
        UserBotLatencyLogObserver(),
        LatencyBreakdownObserver(),
    ],
)
```

- **`MetricsLogObserver`** (built-in) — Logs per-service TTFB, token usage, TTS character counts, and Smart Turn metrics at DEBUG level
- **`UserBotLatencyLogObserver`** (built-in) — Logs end-to-end latency (user stops speaking to bot starts speaking) per turn, plus a summary at call end
- **`LatencyBreakdownObserver`** (custom, in `observers.py`) — Correlates per-component TTFB into a per-turn summary table printed at call end

### Sample Output

Per-service metrics during the call:

```
DEBUG | 📊 [DeepgramSTTService#0] TTFB (nova-3): 0.182s at 14.231s
DEBUG | 📊 [GroqLLMService#0] TTFB (llama-3.3-70b-versatile): 0.078s at 14.491s
DEBUG | 📊 [DeepgramTTSService#0] TTFB (aura-2-theia-en): 0.035s at 14.526s
DEBUG | ⏱️ LATENCY FROM USER STOPPED SPEAKING TO BOT STARTED SPEAKING: 1.432s
```

Summary table at call end:

```
=== LATENCY BREAKDOWN (4 turns) ===
Turn | Total  | STT TTFB | Smart Turn | LLM TTFB | TTS TTFB | LLM Tokens | TTS Chars
-----+--------+----------+------------+----------+----------+------------+----------
   1 | 1.420s |   0.182s |      210ms |   0.078s |   0.035s |    156/42  |      124
   2 | 1.310s |   0.155s |      195ms |   0.072s |   0.031s |    198/38  |      108
   3 | 1.550s |   0.220s |      230ms |   0.085s |   0.042s |    240/55  |      156
   4 | 1.280s |   0.148s |      188ms |   0.068s |   0.029s |    282/32  |       89
-----+--------+----------+------------+----------+----------+------------+----------
 Avg | 1.390s |   0.176s |      206ms |   0.076s |   0.034s |            |
```

## AI Services

| Service | Provider | Model |
|---------|----------|-------|
| LLM | Groq | llama-3.3-70b-versatile |
| STT | Deepgram | nova-3 |
| TTS | Deepgram | aura-2-theia-en |
| VAD | Silero | + Smart Turn v3 (ML-based end-of-turn detection) |

## Prerequisites

- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- A [Groq](https://console.groq.com/) API key
- A [Deepgram](https://deepgram.com/) API key
- [Modal](https://modal.com/) CLI installed and authenticated

## Setup

1. Install the Modal CLI:

   ```sh
   uv tool install modal
   modal setup
   ```

2. Create an `.env` file with your API keys:

   ```sh
   cd latency
   cp env.example .env
   ```

   Fill in the values:

   ```
   GROQ_API_KEY=your_groq_api_key
   DEEPGRAM_API_KEY=your_deepgram_api_key
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   ```

## Deployment

### Modal

```sh
cd latency
modal serve modal_app.py    # dev mode (temporary URL, live reload)
modal deploy modal_app.py   # production (permanent URL)
```

Then configure your Twilio phone number's incoming call webhook to the Modal URL (POST method), the same way as the [inbound bot](../inbound/README.md#configure-twilio).

### Docker

```sh
cd latency
export PROXY_HOST=<tunnel-name>-7860-<region>.devtunnels.ms
docker compose up --build
```

See the [inbound bot README](../inbound/README.md#docker-deployment) for full Docker and dev tunnel setup details.

## Project Structure

```
latency/
  bot.py              # Bot logic with observers wired up
  observers.py        # Custom LatencyBreakdownObserver
  modal_app.py        # Modal deployment config
  pyproject.toml      # Python project config and dependencies
  Dockerfile          # Docker image definition
  docker-compose.yml  # Docker Compose config
  env.example         # Template for .env
```
