import pytest

from eval.comparator import ComparisonResult, compare


def test_identical_transcripts_give_ratio_1():
    result = compare("CA1", ["hello world"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0


def test_completely_different_transcripts_give_low_ratio():
    result = compare("CA1", ["hello world"], ["goodbye moon"], 8000)
    assert result.agreement_ratio < 0.5


def test_segmentation_difference_does_not_penalise():
    # Same words split across turns differently — should still agree fully
    result = compare("CA1", ["hello", "world"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0


def test_audio_duration_from_bytes():
    # 8000 bytes = 1 second of Twilio mulaw (8000 bytes/sec)
    result = compare("CA1", ["test"], ["test"], 8000)
    assert result.audio_duration_seconds == pytest.approx(1.0)


def test_words_per_second():
    # "one two three four" = 4 words over 1 second = 4.0 wps
    result = compare("CA1", ["one two three four"], ["one two three four"], 8000)
    assert result.deepgram_words_per_second == pytest.approx(4.0)
    assert result.azure_words_per_second == pytest.approx(4.0)


def test_word_count_ratio():
    # 3 azure words / 2 deepgram words = 1.5
    result = compare("CA1", ["hi there"], ["hi there you"], 8000)
    assert result.word_count_ratio == pytest.approx(3 / 2)


def test_word_count_ratio_zero_deepgram_returns_zero():
    result = compare("CA1", [], ["hello"], 8000)
    assert result.word_count_ratio == 0.0


def test_deepgram_only_words():
    result = compare("CA1", ["gonna"], ["going to"], 8000)
    assert "gonna" in result.deepgram_only_words


def test_azure_only_words():
    result = compare("CA1", ["gonna"], ["going to"], 8000)
    assert "going" in result.azure_only_words or "to" in result.azure_only_words


def test_turn_counts():
    result = compare("CA1", ["a", "b", "c"], ["x", "y"], 8000)
    assert result.deepgram_turn_count == 3
    assert result.azure_turn_count == 2


def test_zero_audio_bytes_returns_zero_wps():
    result = compare("CA1", ["test"], ["test"], 0)
    assert result.deepgram_words_per_second == 0.0
    assert result.azure_words_per_second == 0.0


def test_punctuation_stripped_in_normalisation():
    result = compare("CA1", ["Hello, world!"], ["hello world"], 8000)
    assert result.agreement_ratio == 1.0
