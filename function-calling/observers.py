#
# Copyright (c) 2025, Jose Reyes
#
# Custom Pipecat observer that collects per-turn latency breakdowns
# across all pipeline components and prints a summary table at call end.
#

import time
from dataclasses import dataclass, field
from statistics import mean
from typing import List, Optional

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    MetricsFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    SmartTurnMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection


@dataclass
class TurnLatency:
    """Latency breakdown for a single conversation turn."""

    turn_number: int
    stt_ttfb: Optional[float] = None
    smart_turn_e2e_ms: Optional[float] = None
    llm_ttfb: Optional[float] = None
    tts_ttfb: Optional[float] = None
    total_wall_clock: Optional[float] = None
    llm_prompt_tokens: Optional[int] = None
    llm_completion_tokens: Optional[int] = None
    tts_characters: Optional[int] = None


class LatencyBreakdownObserver(BaseObserver):
    """Collects per-turn latency data across all pipeline components.

    On each turn: records the wall-clock total from user-stopped-speaking
    to bot-started-speaking, then cross-references MetricsFrame data to
    show the STT TTFB, Smart Turn decision time, LLM TTFB, and TTS TTFB.

    Prints a per-turn summary table at call end.
    """

    def __init__(self):
        super().__init__()
        self._turn_count = 0
        self._user_stopped_time: float = 0.0
        self._current_turn: Optional[TurnLatency] = None
        self._last_completed_turn: Optional[TurnLatency] = None
        self._completed_turns: List[TurnLatency] = []
        self._seen_frame_ids: set = set()
        self._pending_metrics: List[MetricsFrame] = []

    async def on_push_frame(self, data: FramePushed) -> None:
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        # Skip already-processed frames (observers see each frame multiple times
        # as it passes between processors)
        if data.frame.id in self._seen_frame_ids:
            return
        self._seen_frame_ids.add(data.frame.id)

        frame = data.frame

        if isinstance(frame, VADUserStartedSpeakingFrame):
            # User started speaking again — reset any pending timer
            self._user_stopped_time = 0.0

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_stopped_time = time.time()
            self._turn_count += 1
            self._current_turn = TurnLatency(turn_number=self._turn_count)
            self._last_completed_turn = None
            # Flush metrics that arrived before the turn was created (e.g. STT TTFB)
            for mf in self._pending_metrics:
                self._apply_metrics(mf, self._current_turn)
            self._pending_metrics.clear()

        elif isinstance(frame, MetricsFrame):
            if self._current_turn:
                self._apply_metrics(frame, self._current_turn)
            elif self._last_completed_turn:
                # Late-arriving metrics (e.g. LLM usage after BotStartedSpeaking)
                self._apply_metrics(frame, self._last_completed_turn)
            else:
                # Buffer for the next turn (e.g. STT TTFB before VADUserStopped)
                self._pending_metrics.append(frame)

        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._user_stopped_time > 0 and self._current_turn:
                self._current_turn.total_wall_clock = time.time() - self._user_stopped_time
                self._completed_turns.append(self._current_turn)
                self._last_completed_turn = self._current_turn
                self._user_stopped_time = 0.0
                self._current_turn = None

        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._print_summary()

    def _apply_metrics(self, frame: MetricsFrame, turn: TurnLatency) -> None:
        """Route metrics data to the correct field on the given turn."""
        for m in frame.data:
            if isinstance(m, TTFBMetricsData):
                proc = m.processor.lower()
                if "stt" in proc:
                    turn.stt_ttfb = m.value
                elif "llm" in proc:
                    turn.llm_ttfb = m.value
                elif "tts" in proc:
                    turn.tts_ttfb = m.value
            elif isinstance(m, SmartTurnMetricsData) and m.is_complete:
                turn.smart_turn_e2e_ms = m.e2e_processing_time_ms
            elif isinstance(m, LLMUsageMetricsData):
                turn.llm_prompt_tokens = m.value.prompt_tokens
                turn.llm_completion_tokens = m.value.completion_tokens
            elif isinstance(m, TTSUsageMetricsData):
                turn.tts_characters = m.value

    def _print_summary(self) -> None:
        """Print a formatted table of per-turn latency breakdowns."""
        turns = self._completed_turns
        if not turns:
            return

        header = "Turn | Total  | STT TTFB | Smart Turn | LLM TTFB | TTS TTFB | LLM Tokens | TTS Chars"
        sep = "-----+--------+----------+------------+----------+----------+------------+----------"

        lines = [
            "",
            f"=== LATENCY BREAKDOWN ({len(turns)} turn{'s' if len(turns) != 1 else ''}) ===",
            header,
            sep,
        ]

        for t in turns:
            lines.append(
                f" {t.turn_number:>3} "
                f"| {self._fmt_s(t.total_wall_clock)} "
                f"| {self._fmt_s(t.stt_ttfb):>8} "
                f"| {self._fmt_ms(t.smart_turn_e2e_ms):>10} "
                f"| {self._fmt_s(t.llm_ttfb):>8} "
                f"| {self._fmt_s(t.tts_ttfb):>8} "
                f"| {self._fmt_tokens(t.llm_prompt_tokens, t.llm_completion_tokens):>10} "
                f"| {self._fmt_int(t.tts_characters):>8}"
            )

        lines.append(sep)

        # Averages row
        def avg_or_none(vals):
            filtered = [v for v in vals if v is not None]
            return mean(filtered) if filtered else None

        lines.append(
            f" {'Avg':>3} "
            f"| {self._fmt_s(avg_or_none([t.total_wall_clock for t in turns]))} "
            f"| {self._fmt_s(avg_or_none([t.stt_ttfb for t in turns])):>8} "
            f"| {self._fmt_ms(avg_or_none([t.smart_turn_e2e_ms for t in turns])):>10} "
            f"| {self._fmt_s(avg_or_none([t.llm_ttfb for t in turns])):>8} "
            f"| {self._fmt_s(avg_or_none([t.tts_ttfb for t in turns])):>8} "
            f"|            "
            f"|         "
        )

        logger.info("\n".join(lines))

    @staticmethod
    def _fmt_s(val: Optional[float]) -> str:
        return f"{val:.3f}s" if val is not None else "   -  "

    @staticmethod
    def _fmt_ms(val: Optional[float]) -> str:
        return f"{val:.0f}ms" if val is not None else "   -  "

    @staticmethod
    def _fmt_tokens(prompt: Optional[int], completion: Optional[int]) -> str:
        if prompt is not None and completion is not None:
            return f"{prompt}/{completion}"
        return "-"

    @staticmethod
    def _fmt_int(val: Optional[int]) -> str:
        return str(val) if val is not None else "-"
