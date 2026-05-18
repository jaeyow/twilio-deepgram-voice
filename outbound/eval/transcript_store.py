import threading
from typing import TypedDict


class TranscriptEntry(TypedDict):
    deepgram: list[str]
    azure: list[str]
    audio_bytes: int


_store: dict[str, TranscriptEntry] = {}
_lock = threading.Lock()


def get_or_create(call_sid: str) -> TranscriptEntry:
    with _lock:
        if call_sid not in _store:
            _store[call_sid] = {"deepgram": [], "azure": [], "audio_bytes": 0}
        return _store[call_sid]


def append_deepgram(call_sid: str, utterance: str) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["deepgram"].append(utterance)


def append_azure(call_sid: str, utterance: str) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["azure"].append(utterance)


def add_audio_bytes(call_sid: str, count: int) -> None:
    with _lock:
        if call_sid in _store:
            _store[call_sid]["audio_bytes"] += count


def get(call_sid: str) -> TranscriptEntry | None:
    with _lock:
        return _store.get(call_sid)


def remove(call_sid: str) -> None:
    with _lock:
        _store.pop(call_sid, None)
