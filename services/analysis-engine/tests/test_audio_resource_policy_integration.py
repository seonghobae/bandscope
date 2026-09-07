"""Cross-boundary regressions for canonical local-audio resource admission."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from bandscope_analysis.api import validate_analysis_job_request
from bandscope_analysis.audio_resource_policy import (
    DEFAULT_AUDIO_RESOURCE_POLICY,
    AudioResourcePolicy,
)
from bandscope_analysis.separation.audio_separator import (
    AudioSeparationConfig,
    AudioStemSeparator,
)
from bandscope_analysis.temporal.analyzer import TemporalAnalyzer


def _local_request(file_size_bytes: object) -> dict[str, object]:
    """Build one local-audio request whose only variable is encoded byte metadata."""
    return {
        "sourceKind": "local_audio",
        "projectId": "policy-project",
        "sourceLabel": "rehearsal.wav",
        "roleFocus": [],
        "localSource": {
            "sourcePath": "/tmp/rehearsal.wav",
            "fileName": "rehearsal.wav",
            "extension": "wav",
            "fileSizeBytes": file_size_bytes,
        },
    }


@pytest.mark.parametrize(
    "file_size_bytes",
    [True, DEFAULT_AUDIO_RESOURCE_POLICY.max_encoded_file_bytes + 1],
)
def test_request_preflight_rejects_metadata_outside_canonical_policy(
    file_size_bytes: object,
) -> None:
    """Reject impossible/oversized metadata before orchestration starts expensive work."""
    with pytest.raises(ValueError, match="localSource.fileSizeBytes"):
        validate_analysis_job_request(_local_request(file_size_bytes))


def test_request_preflight_accepts_exact_encoded_byte_boundary() -> None:
    """The service API accepts the same exact encoded-byte ceiling as the policy."""
    request = validate_analysis_job_request(
        _local_request(DEFAULT_AUDIO_RESOURCE_POLICY.max_encoded_file_bytes)
    )

    assert (
        request["localSource"]["fileSizeBytes"]
        == DEFAULT_AUDIO_RESOURCE_POLICY.max_encoded_file_bytes
    )


def test_temporal_decoder_probes_one_sample_past_duration_limit_and_fails_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporal decode detects a one-sample-overlong source instead of silently truncating it."""
    import librosa

    policy = AudioResourcePolicy(
        max_encoded_file_bytes=100,
        target_sample_rate=8,
        max_duration_seconds=1.0,
    )
    source = tmp_path / "overlong.wav"
    source.write_bytes(b"bounded")
    monkeypatch.setattr(
        "bandscope_analysis.audio_decode.preflight_audio_metadata",
        lambda *_args, **_kwargs: None,
    )
    captured: dict[str, object] = {}

    def fake_load(fileobj: object, **kwargs: object) -> tuple[np.ndarray, int]:
        captured.update(kwargs)
        return np.zeros(policy.max_decoded_samples + 1, dtype=np.float32), policy.target_sample_rate

    monkeypatch.setattr(librosa, "load", fake_load)
    monkeypatch.setattr(
        librosa.beat,
        "beat_track",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("analysis must not run after policy rejection")
        ),
    )

    with pytest.raises(ValueError, match="audio resource policy"):
        TemporalAnalyzer(resource_policy=policy).analyze(source)

    assert captured["duration"] == pytest.approx(
        (policy.max_decoded_samples + 1) / policy.target_sample_rate
    )
    assert captured["sr"] == policy.target_sample_rate
    assert captured["mono"] is True


@pytest.mark.parametrize(
    "metadata",
    [
        SimpleNamespace(frames=44_100 * 901, samplerate=44_100, channels=2),
        SimpleNamespace(frames=44_100, samplerate=7_999, channels=2),
        SimpleNamespace(frames=44_100, samplerate=44_100, channels=3),
    ],
)
def test_temporal_rejects_source_metadata_before_librosa_decode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: SimpleNamespace,
) -> None:
    """Temporal analysis must inspect source metadata before resampling or truncation."""
    import librosa

    source = tmp_path / "source-metadata.wav"
    source.write_bytes(b"bounded")
    monkeypatch.setattr(
        "bandscope_analysis.audio_metadata.soundfile.info",
        lambda _fileobj: metadata,
    )
    load_mock = Mock(side_effect=AssertionError("source metadata must be checked first"))
    monkeypatch.setattr(librosa, "load", load_mock)

    with pytest.raises(ValueError, match="audio resource policy"):
        TemporalAnalyzer().analyze(source)

    load_mock.assert_not_called()


def test_stem_decoder_probes_one_sample_past_duration_limit_and_fails_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stem separation consumes the same decoded-sample ceiling as temporal analysis."""
    import librosa

    config = AudioSeparationConfig(
        target_sample_rate=8,
        max_file_bytes=100,
        max_duration_seconds=1.0,
    )
    source = tmp_path / "overlong.wav"
    source.write_bytes(b"bounded")
    monkeypatch.setattr(
        "bandscope_analysis.audio_decode.preflight_audio_metadata",
        lambda *_args, **_kwargs: None,
    )
    captured: dict[str, object] = {}

    def fake_load(fileobj: object, **kwargs: object) -> tuple[np.ndarray, int]:
        captured.update(kwargs)
        return np.zeros(9, dtype=np.float32), 8

    monkeypatch.setattr(librosa, "load", fake_load)
    monkeypatch.setattr(
        AudioStemSeparator,
        "_separate_signal",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("model must not run after policy rejection")
        ),
    )

    with pytest.raises(ValueError, match="audio resource policy"):
        AudioStemSeparator(config).separate(source)

    assert captured["duration"] == pytest.approx(9 / 8)
    assert captured["sr"] == 8
    assert captured["mono"] is True


@pytest.mark.parametrize(
    "metadata",
    [
        SimpleNamespace(frames=44_100 * 901, samplerate=44_100, channels=2),
        SimpleNamespace(frames=44_100, samplerate=7_999, channels=2),
        SimpleNamespace(frames=44_100, samplerate=44_100, channels=3),
    ],
)
def test_stem_decoder_rejects_source_metadata_before_librosa_decode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: SimpleNamespace,
) -> None:
    """Stem separation must inspect source metadata before mono conversion or model work."""
    import librosa

    source = tmp_path / "source-metadata.wav"
    source.write_bytes(b"bounded")
    monkeypatch.setattr(
        "bandscope_analysis.audio_metadata.soundfile.info",
        lambda _fileobj: metadata,
    )
    load_mock = Mock(side_effect=AssertionError("source metadata must be checked first"))
    monkeypatch.setattr(librosa, "load", load_mock)

    separator = AudioStemSeparator(AudioSeparationConfig(max_file_bytes=100))
    with pytest.raises(ValueError, match="audio resource policy"):
        separator.separate(source)

    load_mock.assert_not_called()


def test_stem_decoder_rejects_nonfinite_decoded_output_before_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decoder NaN/Inf values fail closed instead of being normalized into model input."""
    import librosa

    source = tmp_path / "nonfinite.wav"
    source.write_bytes(b"bounded")
    monkeypatch.setattr(
        "bandscope_analysis.audio_decode.preflight_audio_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        librosa,
        "load",
        lambda *args, **kwargs: (np.array([0.0, np.nan], dtype=np.float32), 8),
    )
    monkeypatch.setattr(
        AudioStemSeparator,
        "_separate_signal",
        lambda *_: (_ for _ in ()).throw(AssertionError("model must not receive non-finite audio")),
    )

    with pytest.raises(ValueError, match="audio resource policy"):
        AudioStemSeparator(
            AudioSeparationConfig(
                target_sample_rate=8,
                max_file_bytes=100,
                max_duration_seconds=1.0,
            )
        ).separate(source)
