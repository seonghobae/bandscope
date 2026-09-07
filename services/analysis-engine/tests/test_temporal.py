"""Tests for temporal analysis module."""

import warnings
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf  # type: ignore

from bandscope_analysis.temporal import TemporalAnalyzer
from bandscope_analysis.temporal.analyzer import _estimate_downbeats


@pytest.fixture
def dummy_audio_file(tmp_path: Path) -> Path:
    """Create a short dummy audio file (sine wave with a clear beat)."""
    sr = 44100
    duration = 5.0  # 5 seconds to give beat tracker enough data
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    # 440 Hz sine wave + some volume modulation for "beats"
    # A clear 120 BPM transient
    audio = np.zeros_like(t)
    beat_interval = int(sr * 60 / 120)  # 0.5s intervals
    for i in range(0, len(audio), beat_interval):
        end = min(i + int(sr * 0.1), len(audio))
        audio[i:end] = np.sin(2 * np.pi * 100 * t[i:end])  # Drum-like thud

    file_path = tmp_path / "test_audio.wav"
    sf.write(str(file_path), audio, sr)
    return file_path


def test_temporal_analyzer_basic(dummy_audio_file: Path) -> None:
    """Test that the analyzer can decode audio and return valid features."""
    analyzer = TemporalAnalyzer()
    features = analyzer.analyze(dummy_audio_file)

    assert features["sample_rate"] == 44100
    assert features["duration_seconds"] == pytest.approx(5.0, abs=0.1)
    # librosa might not get exactly 120 with short synth data, but should be > 0
    assert features["bpm"] > 0
    assert isinstance(features["beat_times"], list)
    assert isinstance(features["downbeat_times"], list)


def test_temporal_analyzer_file_not_found() -> None:
    """Test that analyzer raises a payload-safe error for missing files."""
    analyzer = TemporalAnalyzer()
    with pytest.raises(FileNotFoundError, match="Audio source is unavailable"):
        analyzer.analyze("nonexistent_file.wav")


def test_temporal_analyzer_missing_file_does_not_call_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing paths should fail before librosa tries fallback decoders."""
    import librosa

    load_mock = Mock(side_effect=AssertionError("librosa.load should not be called"))
    monkeypatch.setattr(librosa, "load", load_mock)

    analyzer = TemporalAnalyzer()
    with pytest.raises(FileNotFoundError, match="Audio source is unavailable"):
        analyzer.analyze("nonexistent_file.wav")
    load_mock.assert_not_called()


def test_temporal_analyzer_directory_does_not_call_decoder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Directory paths should fail before librosa tries fallback decoders."""
    import librosa

    load_mock = Mock(side_effect=AssertionError("librosa.load should not be called"))
    monkeypatch.setattr(librosa, "load", load_mock)

    with pytest.raises(FileNotFoundError, match="Audio source is unavailable"):
        TemporalAnalyzer().analyze(tmp_path)
    load_mock.assert_not_called()


def test_temporal_analyzer_invalid_y_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure temporal analyzer raises ValueError if librosa returns non-ndarray."""
    import librosa

    from bandscope_analysis.temporal.analyzer import TemporalAnalyzer

    def fake_load(*args, **kwargs):
        return "not-an-array", 22050

    monkeypatch.setattr(librosa, "load", fake_load)

    test_wav = tmp_path / "test.wav"
    sf.write(test_wav, np.zeros(4_000, dtype=np.float32), 44_100)

    with pytest.raises(ValueError, match="Expected numpy array"):
        TemporalAnalyzer().analyze(test_wav)


def test_temporal_analyzer_exception_handling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure arbitrary decoder exception payloads are not relayed to callers."""
    import librosa

    from bandscope_analysis.temporal.analyzer import TemporalAnalyzer

    def fake_load(*args: object, **kwargs: object) -> tuple[np.ndarray, int]:
        raise Exception("Mocked general error")

    monkeypatch.setattr(librosa, "load", fake_load)

    test_wav = tmp_path / "test.wav"
    sf.write(test_wav, np.zeros(4_000, dtype=np.float32), 44_100)

    with pytest.raises(ValueError, match=r"^Temporal analysis failed\.$") as exc_info:
        TemporalAnalyzer().analyze(test_wav)
    assert "Mocked general error" not in str(exc_info.value)


def test_temporal_analyzer_rejects_oversized_file(monkeypatch, tmp_path: Path) -> None:
    """Ensure large files are rejected before decode to prevent resource exhaustion."""
    import librosa

    from bandscope_analysis.temporal import analyzer as analyzer_module

    test_wav = tmp_path / "large.wav"
    sf.write(test_wav, np.zeros(4_000, dtype=np.float32), 44_100)

    monkeypatch.setattr(analyzer_module, "MAX_AUDIO_FILE_BYTES", 1)

    def fake_load(*args, **kwargs):
        raise AssertionError("librosa.load should not be called for oversized files")

    monkeypatch.setattr(librosa, "load", fake_load)

    analyzer = TemporalAnalyzer()
    with pytest.raises(ValueError, match="too large"):
        analyzer.analyze(test_wav)


def test_temporal_analyzer_uses_duration_limit(monkeypatch, tmp_path: Path) -> None:
    """Ensure librosa.load receives bounded duration for safer decode behavior."""
    import librosa

    test_wav = tmp_path / "bounded.wav"
    sf.write(test_wav, np.zeros(4_000, dtype=np.float32), 44_100)
    captured_kwargs: dict[str, object] = {}

    def fake_load(path, **kwargs):
        captured_kwargs.update(kwargs)
        return np.zeros(44100, dtype=float), 44100

    monkeypatch.setattr(librosa, "load", fake_load)

    def fake_beat_track(y, sr):
        return np.array([120.0]), np.array([0])

    monkeypatch.setattr(librosa.beat, "beat_track", fake_beat_track)
    monkeypatch.setattr(librosa, "frames_to_time", lambda frames, sr: np.array([0.0]))

    analyzer = TemporalAnalyzer()
    analyzer.analyze(test_wav)

    from bandscope_analysis.temporal.analyzer import MAX_ANALYSIS_DURATION_SECONDS

    assert captured_kwargs["duration"] == MAX_ANALYSIS_DURATION_SECONDS


def test_temporal_analyzer_does_not_suppress_unrelated_loader_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unrelated decoder warnings should remain visible to tests and callers."""
    import librosa

    test_wav = tmp_path / "test.wav"
    sf.write(test_wav, np.zeros(4_000, dtype=np.float32), 44_100)

    def fake_load(*args: object, **kwargs: object) -> tuple[np.ndarray, int]:
        warnings.warn("unrelated downstream warning", FutureWarning, stacklevel=2)
        return np.zeros(1024, dtype=float), 44100

    monkeypatch.setattr(librosa, "load", fake_load)
    monkeypatch.setattr(librosa, "get_duration", lambda *, y, sr: 1.0)
    monkeypatch.setattr(
        librosa.beat,
        "beat_track",
        lambda *, y, sr: (np.array([120.0]), np.array([0, 1, 2, 3])),
    )
    monkeypatch.setattr(
        librosa,
        "frames_to_time",
        lambda frames, *, sr: np.array([0.0, 0.5, 1.0, 1.5]),
    )

    with pytest.warns(FutureWarning, match="unrelated downstream warning"):
        features = TemporalAnalyzer().analyze(test_wav)

    assert features["bpm"] == 120.0


def test_estimate_downbeats_picks_strongest_onset_phase() -> None:
    """Downbeats land on the bar phase with the most onset energy, not index 0."""
    onset = np.full(200, 1.0)
    beat_frames = np.arange(16) * 10
    beat_times = beat_frames * 0.1
    # Accent phase 2 (beats 2, 6, 10, 14) — the old "every 4th from 0" would miss this.
    for i in range(2, 16, 4):
        onset[beat_frames[i]] = 10.0
    downbeats = _estimate_downbeats(onset, beat_frames, beat_times)
    assert downbeats == [float(beat_times[i]) for i in range(2, 16, 4)]


def test_estimate_downbeats_empty() -> None:
    """No beats yields no downbeats."""
    empty = np.array([])
    assert _estimate_downbeats(empty, empty, empty) == []


def test_estimate_downbeats_too_few_beats_returns_first() -> None:
    """Fewer beats than a bar falls back to the first beat as the downbeat."""
    onset = np.ones(50)
    assert _estimate_downbeats(onset, np.array([0, 10]), np.array([0.0, 0.5])) == [0.0]
