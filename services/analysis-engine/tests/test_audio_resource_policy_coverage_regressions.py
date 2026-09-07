"""Coverage regressions for fail-closed audio resource admission branches."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bandscope_analysis.audio_resource_policy import AudioResourcePolicy
from bandscope_analysis.separation.audio_separator import (
    AudioSeparationConfig,
    AudioStemSeparator,
)


def test_policy_rejects_boolean_duration_configuration() -> None:
    """A Boolean duration must not be coerced into a one-second resource budget."""
    with pytest.raises(ValueError, match="audio resource policy"):
        AudioResourcePolicy(max_duration_seconds=True)


def test_policy_rejects_less_than_one_decoded_sample_budget() -> None:
    """A positive duration that represents less than one sample must fail closed."""
    with pytest.raises(ValueError, match="audio resource policy"):
        AudioResourcePolicy(target_sample_rate=1, max_duration_seconds=0.5)


def test_separator_rejects_empty_internal_loader_result_before_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected empty loader result must not reach Demucs inference."""
    audio_path = tmp_path / "unexpected-empty.wav"
    audio_path.write_bytes(b"not-empty")
    separator = AudioStemSeparator(
        AudioSeparationConfig(target_sample_rate=8_000, max_file_bytes=1_000_000)
    )
    monkeypatch.setattr(
        separator,
        "_load_audio",
        lambda _path: (np.array([], dtype=np.float32), 8_000),
    )

    def fail_if_model_runs(_audio: np.ndarray, _sample_rate: int) -> dict[str, np.ndarray]:
        raise AssertionError("empty decoded audio must be rejected before model inference")

    monkeypatch.setattr(separator, "_separate_signal", fail_if_model_runs)

    with pytest.raises(ValueError, match="Stem separation decode failed"):
        separator.separate(audio_path)


def test_separator_rejects_zero_byte_file_before_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-byte selected source must fail before the decoder is invoked."""
    audio_path = tmp_path / "empty.wav"
    audio_path.write_bytes(b"")
    separator = AudioStemSeparator(
        AudioSeparationConfig(target_sample_rate=8_000, max_file_bytes=1_000_000)
    )

    def fail_if_decoder_runs(*_args: object, **_kwargs: object) -> tuple[np.ndarray, int]:
        raise AssertionError("zero-byte input must be rejected before decoder invocation")

    monkeypatch.setattr(
        "bandscope_analysis.audio_decode.librosa.load",
        fail_if_decoder_runs,
    )

    with pytest.raises(ValueError, match="Stem separation decode failed"):
        separator.separate(audio_path)
