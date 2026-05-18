import difflib
import re
import string
from dataclasses import dataclass


@dataclass
class ComparisonResult:
    call_sid: str
    agreement_ratio: float
    word_count_ratio: float
    audio_duration_seconds: float
    deepgram_words_per_second: float
    azure_words_per_second: float
    deepgram_transcript: str
    azure_transcript: str
    deepgram_only_words: list[str]
    azure_only_words: list[str]
    deepgram_turn_count: int
    azure_turn_count: int


def _normalise(utterances: list[str]) -> str:
    text = " ".join(utterances)
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()


def compare(
    call_sid: str,
    deepgram_utterances: list[str],
    azure_utterances: list[str],
    audio_bytes: int,
) -> ComparisonResult:
    deepgram_norm = _normalise(deepgram_utterances)
    azure_norm = _normalise(azure_utterances)

    agreement_ratio = difflib.SequenceMatcher(None, deepgram_norm, azure_norm).ratio()

    deepgram_words = deepgram_norm.split() if deepgram_norm else []
    azure_words = azure_norm.split() if azure_norm else []

    diff = list(difflib.ndiff(deepgram_words, azure_words))
    deepgram_only = [w[2:] for w in diff if w.startswith("- ")]
    azure_only = [w[2:] for w in diff if w.startswith("+ ")]

    audio_duration = audio_bytes / 8000 if audio_bytes > 0 else 0.0
    deepgram_wps = len(deepgram_words) / audio_duration if audio_duration > 0 else 0.0
    azure_wps = len(azure_words) / audio_duration if audio_duration > 0 else 0.0
    word_count_ratio = (
        len(azure_words) / len(deepgram_words) if deepgram_words else 0.0
    )

    return ComparisonResult(
        call_sid=call_sid,
        agreement_ratio=agreement_ratio,
        word_count_ratio=word_count_ratio,
        audio_duration_seconds=audio_duration,
        deepgram_words_per_second=deepgram_wps,
        azure_words_per_second=azure_wps,
        deepgram_transcript=deepgram_norm,
        azure_transcript=azure_norm,
        deepgram_only_words=deepgram_only,
        azure_only_words=azure_only,
        deepgram_turn_count=len(deepgram_utterances),
        azure_turn_count=len(azure_utterances),
    )
