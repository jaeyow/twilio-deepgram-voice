import pytest
from eval import transcript_store


@pytest.fixture(autouse=True)
def cleanup():
    yield
    transcript_store.remove("CA_test")


def test_get_or_create_initialises_empty():
    entry = transcript_store.get_or_create("CA_test")
    assert entry["deepgram"] == []
    assert entry["azure"] == []
    assert entry["audio_bytes"] == 0


def test_get_returns_none_for_unknown():
    assert transcript_store.get("CA_unknown") is None


def test_append_deepgram_stores_utterance():
    transcript_store.get_or_create("CA_test")
    transcript_store.append_deepgram("CA_test", "hello world")
    entry = transcript_store.get("CA_test")
    assert entry["deepgram"] == ["hello world"]


def test_append_azure_stores_utterance():
    transcript_store.get_or_create("CA_test")
    transcript_store.append_azure("CA_test", "hi there")
    entry = transcript_store.get("CA_test")
    assert entry["azure"] == ["hi there"]


def test_add_audio_bytes_accumulates():
    transcript_store.get_or_create("CA_test")
    transcript_store.add_audio_bytes("CA_test", 800)
    transcript_store.add_audio_bytes("CA_test", 400)
    entry = transcript_store.get("CA_test")
    assert entry["audio_bytes"] == 1200


def test_remove_deletes_entry():
    transcript_store.get_or_create("CA_test")
    transcript_store.remove("CA_test")
    assert transcript_store.get("CA_test") is None
