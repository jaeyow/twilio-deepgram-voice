# Twilio Chatbot: Inbound

A Pipecat-based voice bot that handles inbound phone calls via Twilio, deployed to [Modal](https://modal.com).

## How It Works

When someone calls your Twilio number:

1. Twilio sends an HTTP POST to your Modal URL
2. The server responds with TwiML instructing Twilio to open a WebSocket stream
3. The bot parses the WebSocket connection and sets up the Pipecat pipeline
4. Caller information is fetched from Twilio's REST API
5. The bot initiates the conversation and responds in real-time

## Architecture

```
Phone Call <-> Twilio <-> Media Streams (WebSocket) <-> Modal <-> Pipecat <-> AI Services
```

### AI Services

| Service | Provider | Model |
|---------|----------|-------|
| LLM | Groq | llama-3.3-70b-versatile |
| STT | Deepgram | nova-3 |
| TTS | Deepgram | aura-2-theia-en |
| VAD | Silero | + Smart Turn v3 (ML-based end-of-turn detection) |

### Audio Processing

- RNNoise filter for background noise suppression
- 8kHz sample rate (Twilio telephony standard)
- Call recordings saved as WAV files to a Modal Volume

## Prerequisites

- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- A [Groq](https://console.groq.com/) API key
- A [Deepgram](https://deepgram.com/) API key
- [Modal](https://modal.com/) CLI installed and authenticated

## Setup

1. Install the Modal CLI:

   ```sh
   pip install modal
   modal setup
   ```

2. Create an `.env` file with your API keys:

   ```sh
   cd inbound
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

### Dev mode (temporary URL, live reload)

```sh
cd inbound
modal serve modal_app.py
```

### Production (permanent URL)

```sh
cd inbound
modal deploy modal_app.py
```

### Configure Twilio

After deploying, set your Twilio phone number's incoming call webhook:

1. Go to your [Twilio Console](https://console.twilio.com/)
2. Navigate to Phone Numbers > Manage > Active numbers
3. Click on your phone number
4. Under "Voice Configuration", set "A call comes in" to:
   - **Webhook**
   - **URL**: your Modal URL (e.g. `https://<workspace>--twilio-inbound-bot-serve.modal.run/`)
   - **Method**: POST
5. Click "Save configuration"

No TwiML Bin is needed. The Modal app dynamically generates TwiML that routes Twilio's media stream to its own WebSocket endpoint.

## Docker Deployment

You can also run the bot locally using Docker instead of Modal. Since Twilio needs a publicly reachable URL to send webhooks and media streams, you'll need a tunnel. VS Code has built-in [dev tunnels](https://code.visualstudio.com/docs/editor/port-forwarding) that work well for this.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (and Docker Compose)
- [VS Code](https://code.visualstudio.com/) with a GitHub or Microsoft account (for dev tunnels)

### 1. Forward port 7860 with VS Code dev tunnels

Before starting the container, forward port `7860` so you can get the public URL:

1. Open the **Ports** panel in VS Code (View > Open View... > Ports, or the Ports tab in the bottom panel)
2. Click **Forward a Port** and enter `7860`
3. Right-click the forwarded port and set **Port Visibility** to **Public** (Twilio needs unauthenticated access)
4. Copy the **Forwarded Address** (e.g. `https://<tunnel-name>-7860-<region>.devtunnels.ms`)

Extract just the hostname (without `https://`) — you'll need it in the next step.

### 2. Run with Docker Compose

Set the `PROXY_HOST` environment variable to your dev tunnel hostname, then start the container:

```sh
cd inbound
export PROXY_HOST=<tunnel-name>-7860-<region>.devtunnels.ms   # your dev tunnel hostname
docker compose up --build
```

The `PROXY_HOST` is critical — the bot embeds it in the TwiML XML response so Twilio knows where to open the WebSocket media stream (`wss://<PROXY_HOST>/ws`). Without it, calls will fail.

### 3. Configure Twilio

Point your Twilio phone number's incoming call webhook to your dev tunnel URL:

1. Go to your [Twilio Console](https://console.twilio.com/)
2. Navigate to Phone Numbers > Manage > Active numbers
3. Click on your phone number
4. Under "Voice Configuration", set "A call comes in" to:
   - **Webhook**
   - **URL**: your dev tunnel URL (e.g. `https://<tunnel-name>-7860-<region>.devtunnels.ms/`)
   - **Method**: POST
5. Click "Save configuration"

### Alternative: docker run

If you prefer not to use Docker Compose:

```sh
cd inbound
docker build -t twilio-inbound-bot .
docker run --rm \
  --env-file .env \
  -e RECORDINGS_DIR=/recordings \
  -v "$(pwd)/recordings:/recordings" \
  -p 7860:7860 \
  twilio-inbound-bot \
  --transport twilio --host 0.0.0.0 --proxy <tunnel-name>-7860-<region>.devtunnels.ms
```

### Recordings

When running with Docker, recordings are saved to the `./recordings/` directory on your host machine (mounted as a volume).

### Notes

- VS Code dev tunnel URLs are stable for your account, so you won't need to update the Twilio webhook URL between sessions.
- The port visibility **must** be set to **Public**. The default "Private" requires authentication, which Twilio cannot provide.
- The `--proxy` flag tells Pipecat's runner what public hostname to embed in the TwiML response for the WebSocket stream URL. This is different from Modal, where the `modal_app.py` reads the hostname from the incoming request headers.

## Recordings (Modal)

Call recordings are saved as WAV files to a Modal Volume (`pipecat-recordings`).

```sh
# List recordings
modal volume ls pipecat-recordings

# Download a recording
modal volume get pipecat-recordings <filename>.wav .
```

## Project Structure

```
inbound/
  bot.py              # Bot logic: pipeline, AI services, event handlers
  modal_app.py        # Modal deployment: image, secrets, FastAPI routes
  pyproject.toml      # Python project config and dependencies
  uv.lock             # Dependency lockfile (committed for reproducible builds)
  Dockerfile          # Docker image definition
  docker-compose.yml  # Docker Compose config for local development
  pcc-deploy.toml     # Pipecat Cloud deployment config
  .env                # API keys (not committed)
  env.example         # Template for .env
```

## Customization

The bot is configured in `bot.py`. Key areas to customize:

- **System prompt** (line 139): Change the bot's personality and behavior
- **Voice** (line 136): Change the Deepgram TTS voice
- **Turn detection** (line 146): Tune Smart Turn and VAD parameters
- **Idle timeout** (line 158): How long to wait before the bot continues on its own
