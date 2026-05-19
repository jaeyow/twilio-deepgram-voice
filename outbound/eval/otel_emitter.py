from opentelemetry import metrics, trace

from eval.comparator import ComparisonResult

_meter = metrics.get_meter("stt.eval")
_tracer = trace.get_tracer("stt.eval")

_agreement_gauge = _meter.create_gauge("stt.eval.agreement_ratio")
_word_ratio_gauge = _meter.create_gauge("stt.eval.word_count_ratio")
_wps_gauge = _meter.create_gauge("stt.eval.words_per_second")
_duration_gauge = _meter.create_gauge("stt.eval.audio_duration_seconds")

_MAX_TRANSCRIPT_CHARS = 1000


def emit(result: ComparisonResult, to_number: str = "") -> None:
    attrs = {"call_sid": result.call_sid, "to_number": to_number}

    _agreement_gauge.set(result.agreement_ratio, attrs)
    _word_ratio_gauge.set(result.word_count_ratio, attrs)
    _wps_gauge.set(result.deepgram_words_per_second, {**attrs, "stt": "deepgram"})
    _wps_gauge.set(result.azure_words_per_second, {**attrs, "stt": "azure"})
    _duration_gauge.set(result.audio_duration_seconds, attrs)

    with _tracer.start_as_current_span("stt_comparison") as span:
        span.set_attribute("call_sid", result.call_sid)
        span.set_attribute("to_number", to_number)
        span.set_attribute("agreement_ratio", result.agreement_ratio)
        span.set_attribute("word_count_ratio", result.word_count_ratio)
        span.set_attribute("audio_duration_seconds", result.audio_duration_seconds)
        span.set_attribute("deepgram_words_per_second", result.deepgram_words_per_second)
        span.set_attribute("azure_words_per_second", result.azure_words_per_second)
        span.set_attribute(
            "deepgram_transcript", result.deepgram_transcript[:_MAX_TRANSCRIPT_CHARS]
        )
        span.set_attribute(
            "azure_transcript", result.azure_transcript[:_MAX_TRANSCRIPT_CHARS]
        )
        span.set_attribute("deepgram_only_words", str(result.deepgram_only_words))
        span.set_attribute("azure_only_words", str(result.azure_only_words))
        span.set_attribute("deepgram_turn_count", result.deepgram_turn_count)
        span.set_attribute("azure_turn_count", result.azure_turn_count)
