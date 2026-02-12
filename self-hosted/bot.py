#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.observers.loggers.user_bot_latency_log_observer import UserBotLatencyLogObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import WhisperSTTService, Model
from pipecat.services.xtts.tts import XTTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from observers import LatencyBreakdownObserver

load_dotenv(override=True)

# Reduce logging noise from empty audio frame warnings
logger.disable("pipecat.services.stt_service")


async def get_call_info(call_sid: str) -> dict:
    """Fetch call information from Twilio REST API using aiohttp."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return {}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"

    try:
        auth = aiohttp.BasicAuth(account_sid, auth_token)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Twilio API error ({response.status}): {error_text}")
                    return {}

                data = await response.json()

                call_info = {
                    "from_number": data.get("from"),
                    "to_number": data.get("to"),
                }

                return call_info

    except Exception as e:
        logger.error(f"Error fetching call info from Twilio: {e}")
        return {}


async def start_twilio_recording(call_sid: str):
    """Start a Twilio-side recording for the given call via the REST API."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot start recording")
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}/Recordings.json"

    try:
        auth = aiohttp.BasicAuth(account_sid, auth_token)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                auth=auth,
                data={"RecordingChannels": "dual"},
            ) as response:
                if response.status not in (200, 201):
                    error_text = await response.text()
                    logger.error(f"Twilio recording API error ({response.status}): {error_text}")
                    return

                data = await response.json()
                logger.info(f"Twilio recording started: SID={data.get('sid')}")

    except Exception as e:
        logger.error(f"Error starting Twilio recording: {e}")


async def run_bot(transport: BaseTransport, handle_sigint: bool, testing: bool, call_sid: str = ""):
    # --- Self-hosted STT: Faster-Whisper (in-process, no network hop) ---
    # On Modal (GPU): device=cuda, compute_type=float16
    # On Docker/local (CPU): device=cpu, compute_type=int8
    stt = WhisperSTTService(
        model=Model.LARGE_V3_TURBO,
        device=os.getenv("WHISPER_DEVICE", "cuda"),
        compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
    )

    # --- Self-hosted LLM: vLLM or Ollama via OpenAI-compatible API ---
    # On Modal: model=meta-llama/Meta-Llama-3.1-8B-Instruct (HuggingFace name for vLLM)
    # On Docker/local: model=llama3.1:8b (Ollama name)
    llm = OLLamaLLMService(
        model=os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
        base_url=os.getenv("VLLM_BASE_URL", "http://localhost:11434/v1"),
    )

    # --- Self-hosted TTS: XTTS v2 streaming server ---
    aiohttp_session = aiohttp.ClientSession()
    tts = XTTSService(
        voice_id="Claribel Dervla",
        base_url=os.getenv("XTTS_BASE_URL", "http://localhost:8001"),
        aiohttp_session=aiohttp_session,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are Miss Harper, an elementary school teacher in an audio call. "
                "Your output will be converted to audio so don't include special characters in your answers. "
                "You are an expert in answering questions about elementary school subjects like math, science, history, and literature. "
                "If the student asks about math, just give the answer without explaining how you got it. "
                "For other subjects, provide clear and concise explanations suitable for an elementary school student. "
                "If the student is quiet for a while, you will continue teaching by asking them questions or providing "
                "interesting facts related to the current topic. Always keep the conversation engaging and educational. "
                "You are also a storyteller and if asked for a story, you will tell an interesting and age-appropriate story to the student."
            ),
        },
    ]

    smart_turn_params = SmartTurnParams(stop_secs=1.5, pre_speech_ms=0.0)
    turn_analyzer = LocalSmartTurnAnalyzerV3(params=smart_turn_params)

    context = LLMContext(messages)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=turn_analyzer)],
            ),
            user_turn_stop_timeout=2.0,
            user_idle_timeout=5.0,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text (Faster-Whisper, in-process)
            user_aggregator,
            llm,  # LLM (vLLM via OpenAI-compat API)
            tts,  # Text-To-Speech (XTTS v2 streaming)
            transport.output(),  # Websocket output to client
            assistant_aggregator,
        ]
    )

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

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # Start Twilio-level recording.
        if call_sid:
            await start_twilio_recording(call_sid)
        # Kick off the conversation.
        messages.append({"role": "system", "content": "Say hello and introduce yourself as Miss Harper."})
        await task.queue_frames([LLMRunFrame()])

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        logger.info("User idle — prompting bot to continue")
        messages.append({"role": "system", "content": "The student is quiet. Continue teaching."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await aiohttp_session.close()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint, force_gc=True)

    await runner.run(task)


async def bot(runner_args: RunnerArguments, testing: Optional[bool] = False):
    """Main bot entry point compatible with Pipecat Cloud."""

    _, call_data = await parse_telephony_websocket(runner_args.websocket)

    call_info = await get_call_info(call_data["call_id"])
    if call_info:
        logger.info(f"Call from: {call_info.get('from_number')} to: {call_info.get('to_number')}")

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_in_filter=RNNoiseFilter(),
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(start_secs=0.2, stop_secs=0.5)
            ),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, testing, call_sid=call_data["call_id"])


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
