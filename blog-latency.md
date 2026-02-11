# Where Does the Time Go? Measuring Voice-to-Voice Latency with Pipecat

After building the [inbound](https://github.com/jaeyow/twilio-chatbot/tree/main/inbound) and [outbound](https://github.com/jaeyow/twilio-chatbot/tree/main/outbound) voice bots, I kept coming back to the same question: *how fast is this thing, really?*

I could feel it during calls. You stop talking, there's a beat, and then the bot responds. It felt like maybe a second and a half. Not bad - definitely fast enough to have a conversation. But "it feels like about a second and a half" isn't a measurement. And if I wanted to make it faster, I needed to know where that time was actually going.

Is it the speech recognition? The LLM thinking? The text-to-speech? The network round-trips between all these services? Without numbers, I'd just be guessing.

Turns out, the answer was hiding in plain sight. Pipecat had been tracking this data all along - I just wasn't listening.

## The Data Was Already There

Here's something that surprised me. In my bot code, I'd had these two flags set from the very beginning:

```python
task = PipelineTask(
    pipeline,
    params=PipelineParams(
        audio_in_sample_rate=8000,
        audio_out_sample_rate=8000,
        enable_metrics=True,
        enable_usage_metrics=True,
    ),
)
```

See `enable_metrics=True` and `enable_usage_metrics=True`? Those tell [Pipecat](https://docs.pipecat.ai/) to measure the performance of every service in the pipeline - how long Deepgram takes to return the first transcription, how long Groq takes to start generating tokens, how long the TTS takes to produce audio. Every time audio flows through the pipeline, Pipecat wraps these measurements into something called a `MetricsFrame` and sends it downstream.

But here's the thing: nobody was listening. The metrics frames were flowing through the pipeline and getting silently discarded at the end. It's like having a speedometer in your car that works perfectly - you just never look at it.

What I needed was an **observer**.

## Pipecat's Observer Pattern

Pipecat has a clean concept for this: **observers**. An observer is a side-channel listener that sees every frame flowing between processors in the pipeline, but it's not *in* the pipeline. It can't block anything or slow things down. It just watches.

Think of it like a security camera in a factory. The assembly line keeps running exactly the same whether the cameras are on or off. But when they're on, you can see exactly what's happening at every step.

Pipecat ships with several built-in observers, and two of them were exactly what I needed:

- **`MetricsLogObserver`** - Logs every metrics frame to the console: TTFB (time to first byte) for each service, token usage, TTS character counts, Smart Turn decision metrics
- **`UserBotLatencyLogObserver`** - Measures the wall-clock time from when the user stops speaking to when the bot starts speaking, and gives you a summary at the end of the call

Adding them took three lines of code.

## Three Lines to Instant Visibility

Here's the change. I added the observer imports and passed them to the `PipelineTask`:

```python
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.observers.loggers.user_bot_latency_log_observer import UserBotLatencyLogObserver

# ... existing pipeline setup ...

task = PipelineTask(
    pipeline,
    params=PipelineParams(
        audio_in_sample_rate=8000,
        audio_out_sample_rate=8000,
        enable_metrics=True,
        enable_usage_metrics=True,
    ),
    observers=[
        MetricsLogObserver(),
        UserBotLatencyLogObserver(),
    ],
)
```

That's it. No other changes. The pipeline runs exactly the same as before - the observers are purely additive.

Now when I made a test call, the logs lit up with data I'd never seen before:

```
DEBUG | 📊 [DeepgramSTTService#0] TTFB (nova-3): 0.182s at 14.231s
DEBUG | 📊 [LocalSmartTurnAnalyzerV3#0] SMART TURN: COMPLETE (probability: 94.20%, inference: 12.3ms, server: 15.1ms, e2e: 212.4ms) at 14.413s
DEBUG | 📊 [GroqLLMService#0] TTFB (llama-3.3-70b-versatile): 0.078s at 14.491s
DEBUG | 📊 [GroqLLMService#0] LLM TOKEN USAGE (llama-3.3-70b-versatile): prompt: 156, completion: 42, total: 198 at 14.89s
DEBUG | 📊 [DeepgramTTSService#0] TTFB (aura-2-theia-en): 0.035s at 14.526s
DEBUG | 📊 [DeepgramTTSService#0] TTS USAGE (aura-2-theia-en): 124 characters at 14.89s
DEBUG | ⏱️ LATENCY FROM USER STOPPED SPEAKING TO BOT STARTED SPEAKING: 1.432s
```

Let me break down what each line means:

- **STT TTFB: 0.182s** - Deepgram took 182 milliseconds to return the first word of the transcription after receiving audio
- **Smart Turn: 212.4ms e2e** - The turn detection model took 212ms to decide (with 94.2% confidence) that I was actually done speaking, not just pausing
- **LLM TTFB: 0.078s** - Groq took just 78 milliseconds to start generating the first token of the response. That's fast.
- **LLM Token Usage: 156 prompt / 42 completion** - The context size and response length for this turn
- **TTS TTFB: 0.035s** - Deepgram's TTS took 35 milliseconds to start producing audio from the first text it received
- **Total latency: 1.432s** - The wall-clock time from when I stopped talking to when I heard the bot start responding

At the end of the call, `UserBotLatencyLogObserver` printed a summary:

```
INFO | ⏱️ LATENCY FROM USER STOPPED SPEAKING TO BOT STARTED SPEAKING - Avg: 1.432s, Min: 1.234s, Max: 1.687s
```

This was already revealing. But I wanted more detail - specifically, I wanted to see the breakdown *per turn* in a single table, so I could spot patterns across a conversation.

## Building a Custom Observer

The built-in observers are great for a quick look, but their log lines aren't correlated per turn. You can't easily see "for turn 3, what was the STT latency vs. the LLM latency?" To get that, I built a custom observer called `LatencyBreakdownObserver`.

Building a custom observer is straightforward. You subclass `BaseObserver` and implement `on_push_frame`, which gets called every time a frame passes between two processors in the pipeline:

```python
from pipecat.observers.base_observer import BaseObserver, FramePushed

class LatencyBreakdownObserver(BaseObserver):
    async def on_push_frame(self, data: FramePushed) -> None:
        # data.frame is the frame being passed
        # data.source is the processor sending it
        # data.destination is the processor receiving it
        # data.direction is DOWNSTREAM or UPSTREAM
        ...
```

The key insight is that certain frame types mark the boundaries of a conversation turn:

- `VADUserStoppedSpeakingFrame` - The user finished talking. Start the clock.
- `MetricsFrame` - Contains TTFB measurements from each service. Capture them.
- `BotStartedSpeakingFrame` - The bot started talking. Stop the clock.
- `EndFrame` / `CancelFrame` - The call is over. Print the summary.

I created a simple dataclass to hold per-turn data:

```python
@dataclass
class TurnLatency:
    turn_number: int
    stt_ttfb: Optional[float] = None          # seconds
    smart_turn_e2e_ms: Optional[float] = None  # milliseconds
    llm_ttfb: Optional[float] = None           # seconds
    tts_ttfb: Optional[float] = None           # seconds
    total_wall_clock: Optional[float] = None   # seconds
    llm_prompt_tokens: Optional[int] = None
    llm_completion_tokens: Optional[int] = None
    tts_characters: Optional[int] = None
```

The observer collects one `TurnLatency` per conversation turn. When it sees a `VADUserStoppedSpeakingFrame`, it starts a new turn and records the wall-clock time. As `MetricsFrame` objects arrive, it routes each measurement to the right field by checking the processor name (e.g., `"DeepgramSTTService"` for STT, `"GroqLLMService"` for the LLM). When `BotStartedSpeakingFrame` arrives, it stops the clock and saves the turn.

The full implementation is in [observers.py](https://github.com/jaeyow/twilio-chatbot/blob/main/latency/observers.py) - it's about 100 lines of focused code.

I added it alongside the built-in observers:

```python
from observers import LatencyBreakdownObserver

task = PipelineTask(
    pipeline,
    params=PipelineParams(
        audio_in_sample_rate=8000,
        audio_out_sample_rate=8000,
        enable_metrics=True,
        enable_usage_metrics=True,
    ),
    observers=[
        MetricsLogObserver(),
        UserBotLatencyLogObserver(),
        LatencyBreakdownObserver(),
    ],
)
```

## The Numbers: Where Does 1.5 Seconds Go?

Here's what the summary table looks like after a real call. This is the payoff - the table prints to the logs when the call ends:

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

A few things jump out immediately.

**Groq is not the bottleneck.** At 76ms average TTFB for a 70B parameter model, Groq is absurdly fast. If you're worried about LLM latency being the problem, it's not - at least not with Groq.

**The AI services combined take about 500ms.** Add up the averages: STT (176ms) + Smart Turn (206ms) + LLM (76ms) + TTS (34ms) = roughly 490ms. That's the time these services spend doing their thing.

**So where's the other 900ms?** The total averages 1.39 seconds, but only ~490ms is accounted for by service TTFB. The rest is pipeline overhead: network round-trips between your server and each external API (Twilio to Modal, Modal to Deepgram, Modal to Groq, Modal to Deepgram again), audio buffering, frame serialisation, and the VAD's own silence detection window before `VADUserStoppedSpeakingFrame` even fires.

**Turn 3 was slower.** The LLM had to generate a longer response (55 completion tokens vs. 32-42 for other turns), which also meant more TTS characters and slightly higher TTS TTFB. Longer responses take longer - not surprising, but now you can see it in the data.

**Context grows, but TTFB stays flat.** Notice how the prompt token count grows from 156 to 282 across the conversation (each turn adds to the context), but LLM TTFB barely changes (78ms to 68ms). Groq's inference speed doesn't degrade meaningfully with context size at this scale.

## What Would Actually Make It Faster?

Now that we have numbers, we can think about optimisation rationally instead of guessing.

**The biggest single lever is the VAD and turn detection.** The Smart Turn model takes ~200ms to decide you're done talking, and the VAD has a `stop_secs=0.5` parameter that adds 500ms of silence detection before it even triggers. Together, that's roughly 700ms just to establish that you've finished your sentence. You could lower `stop_secs` from 0.5 to 0.3 to shave off 200ms, but you'd get more false positives - the bot would start responding while you're still mid-sentence. It's a tuning tradeoff.

**Network round-trips are the other big factor.** Every external API call adds a round-trip: your server to Deepgram for STT, to Groq for the LLM, to Deepgram again for TTS, plus the Twilio-to-server leg. If your Modal deployment is in `us-east` and your API services are nearby, round-trips are fast. If there's geographic distance, it adds up. Self-hosting these services (running your own STT, LLM, and TTS on the same infrastructure) could eliminate these round-trips entirely - something I'm planning to explore next.

**The AI services themselves are already fast.** Groq at 76ms and Deepgram TTS at 34ms are hard to beat. Deepgram STT at 176ms is decent, though switching to a smaller model (like `nova-2`) might save 20-30ms at the cost of accuracy. The question is whether shaving 20ms off STT is worth lower transcription quality - probably not.

## Try It Yourself

All the code for this - the observers, the bot changes, everything - is in the [`latency/` directory](https://github.com/jaeyow/twilio-chatbot/tree/main/latency) of the GitHub repo. It's a self-contained version of the inbound bot with the observers wired up. Deploy it with:

```sh
cd twilio-chatbot/latency
cp env.example .env
# Fill in your API keys in .env
modal serve modal_app.py
```

Make a call, and you'll see the metrics flowing in your logs. The observers work identically with the outbound bot too - just add the same three imports and the `observers` parameter to the `PipelineTask`.

What I find most interesting about these numbers is what they suggest about the *next* step. The API services are fast, but the network overhead is significant. What if you could run your own STT, LLM, and TTS on the same GPU, eliminating the network hops entirely? Pipecat already supports self-hosted alternatives like Faster-Whisper for STT, vLLM or Ollama for the LLM, and XTTS for TTS. The question is: would self-hosting actually be faster - and at what cost?

That's exactly what I'm going to find out next.
