# Twilio Chatbot: Outbound

A Pipecat-based voice bot that makes outbound phone calls via Twilio, with a FastAPI server for initiating calls and handling WebSocket connections.

## How It Works

When you want to make an outbound call:

1. **Send POST request**: `POST /dialout` with a phone number to call
2. **Server initiates call**: Uses Twilio's REST API to make the outbound call
3. **Call answered**: When answered, Twilio fetches TwiML from your server's `/twiml` endpoint
4. **Server returns TwiML**: Tells Twilio to start a WebSocket stream to your bot
5. **WebSocket connection**: Audio streams between the called person and your bot
6. **Call information**: Phone numbers are passed via TwiML Parameters to your bot

## Architecture

```
curl request → /dialout endpoint → Twilio REST API → Call initiated →
TwiML fetched → WebSocket connection → Bot conversation
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
- Call recordings handled by Twilio (dual-channel, accessible via Twilio Console or API)

## Prerequisites

- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls
- A [Groq](https://console.groq.com/) API key
- A [Deepgram](https://deepgram.com/) API key
- [Modal](https://modal.com/) CLI installed and authenticated (for Modal deployment)
- [Docker](https://docs.docker.com/get-docker/) (and Docker Compose) (for Docker deployment)

## Setup

1. Create an `.env` file with your API keys:

   ```sh
   cd outbound
   cp env.example .env
   ```

   Fill in the values:

   ```
   GROQ_API_KEY=your_groq_api_key
   DEEPGRAM_API_KEY=your_deepgram_api_key
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   LOCAL_SERVER_URL=https://your-tunnel-url
   TO_NUMBER=+15551234567
   FROM_NUMBER=+15559876543
   ```

   - `TO_NUMBER`: The phone number to call (E.164 format)
   - `FROM_NUMBER`: Your Twilio phone number (E.164 format)
   - `LOCAL_SERVER_URL`: Only needed for Docker deployment (not Modal)

## Modal Deployment

Modal provides a serverless cloud deployment. The Modal app dynamically derives all URLs from request headers, so no `LOCAL_SERVER_URL` is needed — just the four API keys.

### 1. Install the Modal CLI

```sh
pip install modal
modal setup
```

### 2. Deploy

```sh
cd outbound
modal serve modal_app.py    # dev mode (temporary URL, live reload)
modal deploy modal_app.py   # production (permanent URL)
```

Modal will print the deployment URL, e.g. `https://<workspace>--twilio-outbound-bot-serve.modal.run`.

### 3. Make an outbound call

Using the test script:

```sh
cd scripts
uv run python test_call.py https://<workspace>--twilio-outbound-bot-serve.modal.run
```

Or with curl:

```sh
curl -X POST https://<workspace>--twilio-outbound-bot-serve.modal.run/dialout \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "+15551234567",
    "from_number": "+15559876543"
  }'
```

> Note: the `from_number` must be a phone number owned by your Twilio account.

## Docker Deployment

Since Twilio needs a publicly reachable URL to send webhooks and media streams, you'll need a tunnel. VS Code has built-in [dev tunnels](https://code.visualstudio.com/docs/editor/port-forwarding) that work well for this.

### 1. Forward port 7860 with VS Code dev tunnels

Before starting the container, forward port `7860` so you can get the public URL:

1. Open the **Ports** panel in VS Code (View > Open View... > Ports, or the Ports tab in the bottom panel)
2. Click **Forward a Port** and enter `7860`
3. Right-click the forwarded port and set **Port Visibility** to **Public** (Twilio needs unauthenticated access)
4. Copy the **Forwarded Address** (e.g. `https://<tunnel-name>-7860-<region>.devtunnels.ms`)

### 2. Update `.env` with your tunnel URL

Set `LOCAL_SERVER_URL` to your dev tunnel URL:

```
LOCAL_SERVER_URL=https://<tunnel-name>-7860-<region>.devtunnels.ms
```

This URL is used in two places:
- The `/dialout` endpoint tells Twilio to fetch TwiML from `LOCAL_SERVER_URL/twiml`
- The TwiML response tells Twilio to open a WebSocket to `wss://<host>/ws`

### 3. Run with Docker Compose

```sh
cd outbound
docker compose up --build
```

The server will start on port 7860.

### 4. Make an outbound call

With the server running, initiate a call using the test script (reads `TO_NUMBER` and `FROM_NUMBER` from `.env`):

```sh
cd scripts
uv run python test_call.py
```

Or with curl:

```sh
curl -X POST https://<your-tunnel-url>/dialout \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "+15551234567",
    "from_number": "+15559876543"
  }'
```

> Note: the `from_number` must be a phone number owned by your Twilio account

### Alternative: docker run

If you prefer not to use Docker Compose:

```sh
cd outbound
docker build -t twilio-outbound-bot .
docker run --rm \
  --env-file .env \
  -p 7860:7860 \
  twilio-outbound-bot
```

### Notes

- VS Code dev tunnel URLs are stable for your account, so you won't need to update `LOCAL_SERVER_URL` between sessions.
- The port visibility **must** be set to **Public**. The default "Private" requires authentication, which Twilio cannot provide.
- Unlike inbound calling, outbound calls don't require webhook configuration in the Twilio console. The server makes direct API calls to Twilio to initiate calls.

## Accessing Call Information in Your Bot

Your bot automatically receives call information through Twilio Stream Parameters. The phone numbers (`to_number` and `from_number`) are passed as parameters and extracted by the `parse_telephony_websocket` function.

You can extend the `DialoutRequest` model in `server_utils.py` to include additional custom data (customer info, campaign data, etc.) and pass it through as stream parameters for personalized conversations.

## Project Structure

```
outbound/
  bot.py              # Bot logic: pipeline, AI services, event handlers
  server.py           # FastAPI server: /dialout, /twiml, /ws endpoints (Docker)
  server_utils.py     # Twilio helpers: call initiation, TwiML generation (Docker)
  modal_app.py        # Modal deployment: image, secrets, FastAPI routes
  pyproject.toml      # Python project config and dependencies
  uv.lock             # Dependency lockfile (committed for reproducible builds)
  Dockerfile          # Docker image definition
  docker-compose.yml  # Docker Compose config for local development
  pcc-deploy.toml     # Pipecat Cloud deployment config
  .env                # API keys (not committed)
  env.example         # Template for .env
scripts/
  test_call.py        # Script to trigger an outbound call (accepts optional URL arg)
  pyproject.toml      # Script dependencies (httpx, python-dotenv)
```

## Customization

The bot is configured in `bot.py`. Key areas to customize:

- **System prompt** (line 97): Change the bot's personality and behavior
- **Voice** (line 93): Change the Deepgram TTS voice
- **Turn detection** (line 105): Tune Smart Turn and VAD parameters
- **Idle timeout** (line 115): How long to wait before the bot continues on its own
