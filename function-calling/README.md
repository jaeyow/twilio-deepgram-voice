# Twilio Chatbot: Function Calling

A Pipecat-based voice bot that extends Miss Harper with LLM function calling — enabling her to take actions mid-conversation like checking her schedule, looking up word definitions, and texting lesson summaries. Based on the [latency bot](../latency/) with the same API stack and latency observers.

## Architecture

Same pipeline as the latency bot, with three tools registered on the LLM:

```
Phone Call <-> Twilio <-> Bot (Modal)
                          ├── Deepgram STT (nova-3)
                          ├── Groq LLM (llama-3.3-70b) + function calling
                          │   ├── get_class_schedule()  → mock data
                          │   ├── lookup_word()          → Dictionary API
                          │   └── send_lesson_summary()  → Twilio SMS
                          ├── Deepgram TTS (aura-2-theia-en)
                          ├── Silero VAD + Smart Turn v3
                          └── Latency observers
```

### Tools

| Tool | Pattern | Description |
|------|---------|-------------|
| `get_class_schedule` | Mock data | Returns today's class schedule with subjects and times |
| `lookup_word` | External API | Fetches word definitions from the [Free Dictionary API](https://dictionaryapi.dev/) |
| `send_lesson_summary` | Side effect | Sends a lesson summary via SMS using the Twilio REST API |

### How Function Calling Works

1. The student asks a question (e.g. "What does photosynthesis mean?")
2. The LLM recognises it should use a tool and generates a function call
3. Pipecat executes the registered handler (e.g. calls the Dictionary API)
4. The result is fed back to the LLM
5. The LLM incorporates the result into a spoken response

Tools with parameters are registered using Pipecat's `register_direct_function`, which auto-extracts the schema from the function signature and docstring. `get_class_schedule` uses the lower-level `register_function` API with a manual `FunctionSchema` — a workaround for a Groq quirk where parameter-free tools receive `arguments=null` instead of `{}`.

## Prerequisites

- A [Twilio](https://www.twilio.com/) account with a phone number that supports voice calls and SMS
- A [Groq](https://console.groq.com/) API key
- A [Deepgram](https://console.deepgram.com/) API key
- [Modal](https://modal.com/) CLI installed and authenticated (for Modal deployment)

## Setup

1. Install the Modal CLI:

   ```sh
   uv tool install modal
   modal setup
   ```

2. Create an `.env` file:

   ```sh
   cd function-calling
   cp env.example .env
   ```

   Fill in the values:

   ```
   GROQ_API_KEY=your_groq_api_key
   DEEPGRAM_API_KEY=your_deepgram_api_key
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   TWILIO_PHONE_NUMBER=+1234567890
   ```

   The `TWILIO_PHONE_NUMBER` is the "From" number for sending SMS. This must be a Twilio number in your account that is SMS-capable.

## Deployment

### Modal

```sh
cd function-calling
modal serve modal_app.py    # dev mode (temporary URL, live reload)
modal deploy modal_app.py   # production (permanent URL)
```

### Docker

```sh
cd function-calling
export PROXY_HOST=<tunnel-name>-7860-<region>.devtunnels.ms
docker compose up --build
```

See the [inbound bot README](../inbound/README.md#docker-deployment) for dev tunnel setup details.

### Configure Twilio

Set your Twilio phone number's incoming call webhook to the bot's URL (POST method).

## Testing the Tools

Once deployed, call the bot and try:

- **Schedule**: "What's on the schedule today?" or "What's the next class?"
- **Dictionary**: "What does photosynthesis mean?" or "Can you look up the word metamorphosis?"
- **SMS**: "Can you send me a summary of what we learned?"

## Project Structure

```
function-calling/
  bot.py              # Bot logic with function calling (based on latency bot)
  tools.py            # Three tool functions + registration helper
  modal_app.py        # Modal deployment config
  observers.py        # Custom LatencyBreakdownObserver (from latency/)
  pyproject.toml      # Python project config and dependencies
  Dockerfile          # Docker image for the bot
  docker-compose.yml  # Local deployment
  env.example         # Template for .env
```
