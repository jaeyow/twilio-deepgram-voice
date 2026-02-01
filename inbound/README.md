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

## Recordings

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
  bot.py          # Bot logic: pipeline, AI services, event handlers
  modal_app.py    # Modal deployment: image, secrets, FastAPI routes
  .env            # API keys (not committed)
  env.example     # Template for .env
```

## Customization

The bot is configured in `bot.py`. Key areas to customize:

- **System prompt** (line 139): Change the bot's personality and behavior
- **Voice** (line 136): Change the Deepgram TTS voice
- **Turn detection** (line 146): Tune Smart Turn and VAD parameters
- **Idle timeout** (line 158): How long to wait before the bot continues on its own
