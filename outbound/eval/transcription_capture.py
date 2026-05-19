from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from eval import transcript_store


class TranscriptionCapture(FrameProcessor):
    """Pass-through processor that records Deepgram transcriptions into the eval store.

    Sits between the STT service and the user aggregator. All frames pass through
    unchanged; TranscriptionFrame text is side-copied to transcript_store.
    """

    def __init__(self, call_sid: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._call_sid = call_sid

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text:
            transcript_store.append_deepgram(self._call_sid, frame.text)
        await self.push_frame(frame, direction)
