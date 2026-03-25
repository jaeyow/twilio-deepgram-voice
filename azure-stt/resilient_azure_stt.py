"""Resilient Azure STT service with automatic session reconnection.

The Azure Speech SDK silently cancels the recognition session when the underlying
WebSocket connection to Azure degrades or times out. Without intervention this
starves the Pipecat pipeline — no transcripts reach the LLM and the bot goes
unresponsive.

This module subclasses AzureSTTService and adds:
  - A `canceled` event handler (the parent registers none)
  - Exponential back-off reconnection on CancellationReason.Error, capped at a
    maximum interval, then sustained indefinite retrying until the call ends
  - A retry counter that resets after successful recognition
  - Safe teardown — no reconnection if the pipeline is intentionally stopping

Retry strategy
--------------
We never give up while the call is active. The call ending (user hanging up)
triggers stop()/cancel() which sets _shutting_down=True and halts the loop.

Back-off schedule (default):
  Attempt 1:  sleep  1s
  Attempt 2:  sleep  2s
  Attempt 3:  sleep  4s
  Attempt 4:  sleep  8s
  Attempt 5+: sleep 30s  (capped — retries indefinitely at this interval)

A 30-second Azure outage is fully covered: if the session recovers at any point,
the next attempt will succeed and the call continues as if nothing happened.
"""

import asyncio

from loguru import logger
from pipecat.frames.frames import CancelFrame, EndFrame, StartFrame
from pipecat.services.azure.stt import AzureSTTService

try:
    from azure.cognitiveservices.speech import (
        CancellationDetails,
        CancellationReason,
        ResultReason,
        SpeechRecognizer,
    )
    from azure.cognitiveservices.speech.audio import (
        AudioStreamFormat,
        PushAudioInputStream,
    )
    from azure.cognitiveservices.speech.dialog import AudioConfig
except ModuleNotFoundError as e:
    raise Exception(f"Missing module: {e}")


class ResilientAzureSTTService(AzureSTTService):
    """AzureSTTService with automatic reconnection on session cancellation.

    Args:
        base_backoff_secs: Initial back-off delay in seconds. Doubles each attempt
            until max_backoff_secs is reached, then holds at that interval.
        max_backoff_secs: Upper bound on the retry interval. Once reached, retries
            continue indefinitely at this fixed interval until the call ends.
        All other kwargs are forwarded to AzureSTTService.
    """

    def __init__(
        self,
        *,
        base_backoff_secs: float = 1.0,
        max_backoff_secs: float = 30.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_backoff_secs = base_backoff_secs
        self._max_backoff_secs = max_backoff_secs
        self._shutting_down = False
        self._reconnecting = False
        self._attempt = 0

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    async def start(self, frame: StartFrame):
        """Start recognition and wire the canceled callback."""
        await super().start(frame)
        if self._speech_recognizer:
            self._speech_recognizer.canceled.connect(self._on_canceled)

    async def stop(self, frame: EndFrame):
        """Signal intentional teardown before delegating to parent."""
        self._shutting_down = True
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        """Signal intentional teardown before delegating to parent."""
        self._shutting_down = True
        await super().cancel(frame)

    # ------------------------------------------------------------------
    # Recognition callback override — reset retry counter on success
    # ------------------------------------------------------------------

    def _on_handle_recognized(self, event):
        """Delegate to parent, and reset attempt counter after reconnection."""
        if event.result.reason == ResultReason.RecognizedSpeech and self._attempt > 0:
            logger.info("Azure STT: recognition restored — resetting retry counter")
            self._attempt = 0
        super()._on_handle_recognized(event)

    # ------------------------------------------------------------------
    # Canceled callback — called by the Azure SDK on a background thread
    # ------------------------------------------------------------------

    def _on_canceled(self, event):
        details = CancellationDetails(event.result)

        if details.reason == CancellationReason.EndOfStream:
            # Normal end during intentional teardown — nothing to do.
            logger.debug("Azure STT: session ended (EndOfStream)")
            return

        logger.error(
            f"Azure STT: session canceled — "
            f"reason={details.reason}, "
            f"code={details.error_code}, "
            f"details={details.error_details}"
        )

        if self._shutting_down or self._reconnecting:
            return

        # Schedule reconnection on the asyncio event loop. The Azure SDK
        # fires callbacks on a background thread, so we can't await directly.
        asyncio.run_coroutine_threadsafe(self._reconnect(), self.get_event_loop())

    # ------------------------------------------------------------------
    # Reconnection with exponential back-off
    # ------------------------------------------------------------------

    async def _reconnect(self):
        """Attempt to re-establish the Azure STT session.

        Retries indefinitely with exponential back-off capped at max_backoff_secs.
        The loop only exits on success or when the pipeline is intentionally torn
        down (_shutting_down=True). A concurrent call is a no-op.

        We never give up while the call is active — the user hanging up (which
        triggers stop()/cancel()) is the only thing that should end the loop.
        """
        if self._shutting_down or self._reconnecting:
            return

        self._reconnecting = True
        try:
            while not self._shutting_down:
                backoff = min(
                    self._base_backoff_secs * (2 ** self._attempt),
                    self._max_backoff_secs,
                )
                self._attempt += 1
                logger.warning(
                    f"Azure STT: reconnecting in {backoff:.0f}s (attempt {self._attempt})"
                )

                # Tear down the dead session before sleeping so we're not
                # holding a broken recognizer during the back-off window.
                self._teardown_recognizer()

                await asyncio.sleep(backoff)

                if self._shutting_down:
                    return

                try:
                    self._setup_recognizer()
                    logger.info("Azure STT: session reconnected successfully")
                    return  # Success — exit. _on_canceled handles future failures.
                except Exception as e:
                    logger.error(
                        f"Azure STT: reconnection attempt {self._attempt} failed: {e}"
                    )
                    # Continue loop — next iteration will use a longer (or capped) back-off.
        finally:
            self._reconnecting = False

    def _teardown_recognizer(self):
        """Stop and discard the current recognizer and audio stream."""
        if self._speech_recognizer:
            try:
                self._speech_recognizer.stop_continuous_recognition_async()
            except Exception:
                pass
            self._speech_recognizer = None

        if self._audio_stream:
            try:
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None

    def _setup_recognizer(self):
        """Create a fresh recognizer session and start continuous recognition."""
        stream_format = AudioStreamFormat(samples_per_second=self.sample_rate, channels=1)
        self._audio_stream = PushAudioInputStream(stream_format)
        audio_config = AudioConfig(stream=self._audio_stream)

        self._speech_recognizer = SpeechRecognizer(
            speech_config=self._speech_config, audio_config=audio_config
        )
        self._speech_recognizer.recognizing.connect(self._on_handle_recognizing)
        self._speech_recognizer.recognized.connect(self._on_handle_recognized)
        self._speech_recognizer.canceled.connect(self._on_canceled)
        self._speech_recognizer.start_continuous_recognition_async()
