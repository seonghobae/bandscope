"""Privacy regressions for temporal-analysis failure diagnostics."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from bandscope_analysis.temporal import TemporalAnalyzer


def test_missing_temporal_source_does_not_disclose_local_path(tmp_path: Path) -> None:
    """Missing-file failures must not echo an absolute customer path to callers."""
    sensitive_path = tmp_path / "private-customer-session" / "unreleased-song.wav"

    with pytest.raises(FileNotFoundError) as exc_info:
        TemporalAnalyzer().analyze(sensitive_path)

    message = str(exc_info.value)
    assert message == "Audio source is unavailable for temporal analysis."
    assert str(sensitive_path) not in message
    assert "unreleased-song.wav" not in message


def test_decoder_failure_redacts_source_path_and_decoder_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Decoder diagnostics must remain useful without logging customer path/payload data."""
    import librosa

    sensitive_path = tmp_path / "private-customer-session" / "unreleased-song.wav"
    sensitive_path.parent.mkdir()
    sf.write(sensitive_path, np.zeros(4_000, dtype=np.float32), 44_100)
    decoder_payload = "decoder exposed /private/customer/token-shaped-audio-name.wav"

    def fail_decode(*args: object, **kwargs: object) -> tuple[object, int]:
        raise RuntimeError(decoder_payload)

    monkeypatch.setattr(librosa, "load", fail_decode)
    caplog.set_level(logging.INFO, logger="bandscope_analysis.temporal.analyzer")

    with pytest.raises(ValueError) as exc_info:
        TemporalAnalyzer().analyze(sensitive_path)

    message = str(exc_info.value)
    assert message == "Temporal analysis failed."
    assert str(sensitive_path) not in message
    assert decoder_payload not in message
    assert str(sensitive_path) not in caplog.text
    assert "unreleased-song.wav" not in caplog.text
    assert decoder_payload not in caplog.text
    assert "RuntimeError" in caplog.text
