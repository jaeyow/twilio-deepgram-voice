from unittest.mock import MagicMock, patch

import pytest

from eval.comparator import ComparisonResult


def _make_result(**overrides) -> ComparisonResult:
    defaults = dict(
        call_sid="CA123",
        agreement_ratio=0.87,
        word_count_ratio=0.95,
        audio_duration_seconds=12.5,
        deepgram_words_per_second=2.3,
        azure_words_per_second=2.1,
        deepgram_transcript="hello how are you",
        azure_transcript="hello how are you",
        deepgram_only_words=["gonna"],
        azure_only_words=["going", "to"],
        deepgram_turn_count=3,
        azure_turn_count=4,
    )
    defaults.update(overrides)
    return ComparisonResult(**defaults)


def test_emit_calls_gauge_set():
    mock_gauge = MagicMock()
    mock_meter = MagicMock()
    mock_meter.create_gauge.return_value = mock_gauge
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._meter", mock_meter),
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", mock_gauge),
        patch("eval.otel_emitter._wps_gauge", mock_gauge),
        patch("eval.otel_emitter._duration_gauge", mock_gauge),
    ):
        from eval.otel_emitter import emit
        result = _make_result()
        emit(result, to_number="+61400000000")

    assert mock_gauge.set.called


def test_emit_sets_agreement_ratio_value():
    mock_gauge = MagicMock()
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", MagicMock()),
        patch("eval.otel_emitter._wps_gauge", MagicMock()),
        patch("eval.otel_emitter._duration_gauge", MagicMock()),
    ):
        from eval import otel_emitter
        result = _make_result(agreement_ratio=0.87)
        otel_emitter.emit(result, to_number="+61400000000")

    # agreement_gauge.set is called with (value, attrs) — check value is 0.87
    set_call_args = [call.args[0] for call in mock_gauge.set.call_args_list]
    assert 0.87 in set_call_args


def test_emit_span_sets_call_sid():
    mock_gauge = MagicMock()
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("eval.otel_emitter._tracer", mock_tracer),
        patch("eval.otel_emitter._agreement_gauge", mock_gauge),
        patch("eval.otel_emitter._word_ratio_gauge", mock_gauge),
        patch("eval.otel_emitter._wps_gauge", mock_gauge),
        patch("eval.otel_emitter._duration_gauge", mock_gauge),
    ):
        from eval import otel_emitter
        result = _make_result(call_sid="CA999")
        otel_emitter.emit(result, to_number="+61400000000")

    call_args_list = mock_span.set_attribute.call_args_list
    attr_names = [args[0][0] for args in call_args_list]
    assert "call_sid" in attr_names
