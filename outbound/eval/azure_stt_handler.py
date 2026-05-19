import asyncio
import audioop
import base64
import json
import os

import azure.cognitiveservices.speech as speechsdk
from loguru import logger

from eval import comparator, otel_emitter, transcript_store


async def run_azure_stt_from_queue(queue: asyncio.Queue) -> None:
    """Read Twilio media messages from queue, stream to Azure STT, compare at call end."""
    call_sid: str | None = None
    to_number: str = ""

    speech_config = speechsdk.SpeechConfig(
        subscription=os.environ["AZURE_SPEECH_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
    )
    # Twilio mulaw is decoded to 16-bit PCM at 8kHz before pushing to Azure
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=8000,
        bits_per_sample=16,
        channels=1,
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if (
            evt.result.reason == speechsdk.ResultReason.RecognizedSpeech
            and call_sid
            and evt.result.text.strip()
        ):
            transcript_store.append_azure(call_sid, evt.result.text.strip())

    recognizer.recognized.connect(on_recognized)
    recognizer.start_continuous_recognition()

    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break  # sentinel: WebSocket closed

            if msg.get("type") != "websocket.receive":
                continue

            text = msg.get("text", "")
            if not text:
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            event = data.get("event")

            if event == "start":
                call_sid = data["start"]["callSid"]
                params = data["start"].get("customParameters", {})
                to_number = params.get("to_number", "")
                transcript_store.get_or_create(call_sid)
                logger.info(f"Azure STT eval started for call {call_sid}")

            elif event == "media" and call_sid:
                raw = base64.b64decode(data["media"]["payload"])
                # G.711 mulaw → 16-bit linear PCM (audioop available in Python ≤ 3.12)
                pcm = audioop.ulaw2lin(raw, 2)
                push_stream.write(pcm)
                transcript_store.add_audio_bytes(call_sid, len(raw))

    except Exception as e:
        logger.error(f"Azure STT handler error: {e}")

    finally:
        push_stream.close()
        # Wait 3 s to let the Deepgram pipeline flush its last frames before comparing
        await asyncio.sleep(3.0)
        recognizer.stop_continuous_recognition()

        if call_sid:
            entry = transcript_store.get(call_sid)
            if entry:
                result = comparator.compare(
                    call_sid=call_sid,
                    deepgram_utterances=list(entry["deepgram"]),
                    azure_utterances=list(entry["azure"]),
                    audio_bytes=entry["audio_bytes"],
                )
                logger.info(
                    f"STT eval complete — call={call_sid} "
                    f"agreement={result.agreement_ratio:.2f} "
                    f"word_ratio={result.word_count_ratio:.2f}"
                )
                otel_emitter.emit(result, to_number=to_number)
                transcript_store.remove(call_sid)
