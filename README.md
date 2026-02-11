# Twilio Voice Bot

Voice bots that integrate with Twilio's Programmable Voice API using [Pipecat](https://docs.pipecat.ai). Real-time audio processing over Twilio Media Streams (WebSocket).

## Examples

### [Inbound Calling](./inbound/)

Handles incoming phone calls. Users call your Twilio number and interact with a voice bot. Deployed to [Modal](https://modal.com).

### [Outbound Calling](./outbound/)

Initiates outbound phone calls programmatically where your bot calls users. Deployable to [Modal](https://modal.com) or locally via Docker Compose.

### [Latency Instrumentation](./latency/)

Measures voice-to-voice latency using Pipecat's observer system. Breaks down per-component TTFB (STT, Smart Turn, LLM, TTS) into a per-turn summary table. Built on the inbound bot.

## Architecture

```
Phone Call <-> Twilio <-> Media Streams (WebSocket) <-> Pipecat <-> AI Services
```

| Component | Purpose |
|-----------|---------|
| Twilio | Phone call routing and audio transport |
| Media Streams | Real-time bidirectional audio over WebSocket |
| Pipecat | Audio processing pipeline and AI service orchestration |
| Groq | LLM inference (llama-3.3-70b-versatile) |
| Deepgram | Speech-to-text (nova-3) and text-to-speech (aura-2) |
| Silero | Voice activity detection + Smart Turn v3 end-of-turn detection |

## Getting Started

Each bot has its own README with setup and deployment instructions:

- [Inbound setup](./inbound/README.md) — Modal deployment
- [Outbound setup](./outbound/README.md) — Modal or Docker deployment
- [Latency setup](./latency/README.md) — Modal or Docker deployment

## Links

- [Pipecat Documentation](https://docs.pipecat.ai)
- [Twilio Documentation](https://www.twilio.com/docs)
- [Modal Documentation](https://modal.com/docs)
