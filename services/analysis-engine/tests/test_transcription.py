"""Tests for transcription API."""

from __future__ import annotations

import io
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from bandscope_analysis.transcription import api as transcription_api
from bandscope_analysis.transcription.api import NoteEvent, transcribe_bass_stem

SAMPLE_RATE = 22050


@dataclass(frozen=True)
class ExpectedNote:
    """Expected note event used for onset/pitch scoring."""

    pitch: str
    start_time: float
    duration: float


def test_transcribe_bass_stem_detects_synthetic_notes() -> None:
    """Transcribe a rendered bass line instead of accepting canned output."""
    expected = [
        ExpectedNote("E2", 0.0, 0.45),
        ExpectedNote("A2", 0.55, 0.45),
        ExpectedNote("D3", 1.10, 0.45),
    ]
    stem_data = _render_bass_sequence(expected)

    events = transcribe_bass_stem(stem_data)

    assert events
    assert all(isinstance(event, NoteEvent) for event in events)
    assert _onset_pitch_f1(events, expected) > 0.95


def test_transcribe_bass_stem_empty() -> None:
    """Test empty stem input returns empty list."""
    events = transcribe_bass_stem(b"")
    assert events == []


def test_transcribe_bass_stem_silence() -> None:
    """Test silent audio returns no note events."""
    silence = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)

    events = transcribe_bass_stem(_wav_bytes(silence))

    assert events == []


def test_transcribe_bass_stem_rejects_oversized_input(monkeypatch) -> None:
    """Reject byte payloads before decoding when they exceed the configured cap."""
    monkeypatch.setattr(transcription_api, "MAX_STEM_BYTES", 2)

    with np.testing.assert_raises(ValueError):
        transcribe_bass_stem(b"abc")


@pytest.mark.parametrize(
    "metadata",
    [
        SimpleNamespace(frames=22050 * 121, samplerate=22050, channels=2),
        SimpleNamespace(frames=22050, samplerate=7_999, channels=2),
        SimpleNamespace(frames=22050, samplerate=22050, channels=3),
    ],
)
def test_transcribe_bass_stem_rejects_source_metadata_before_decode(
    monkeypatch: pytest.MonkeyPatch,
    metadata: SimpleNamespace,
) -> None:
    """Bass transcription must validate source duration, rate, and channels before librosa."""
    monkeypatch.setattr(
        "bandscope_analysis.audio_metadata.soundfile.info",
        lambda _fileobj: metadata,
    )
    load_mock = Mock(side_effect=AssertionError("source metadata must be checked first"))
    monkeypatch.setattr(transcription_api.librosa, "load", load_mock)

    with pytest.raises(ValueError, match="audio resource policy"):
        transcribe_bass_stem(b"not-a-real-wav")

    load_mock.assert_not_called()


def test_transcribe_bass_stem_wraps_pitch_tracking_parameter_errors(monkeypatch) -> None:
    """Return a stable ValueError when pYIN rejects decoded audio parameters."""
    stem_data = _render_bass_sequence([ExpectedNote("E2", 0.0, 0.45)])

    def raise_parameter_error(*_args, **_kwargs):
        """Raise the same exception class librosa.pyin uses for bad inputs."""
        raise transcription_api.librosa.util.exceptions.ParameterError("bad frame")

    monkeypatch.setattr(transcription_api.librosa, "pyin", raise_parameter_error)

    with np.testing.assert_raises(ValueError):
        transcribe_bass_stem(stem_data)


def test_transcribe_bass_stem_returns_empty_when_pyin_has_no_frames(monkeypatch) -> None:
    """Return no events when pitch tracking cannot produce frame arrays."""
    stem_data = _render_bass_sequence([ExpectedNote("E2", 0.0, 0.45)])

    def no_pitch_frames(*_args, **_kwargs):
        """Return the empty pYIN shape handled by the API."""
        return None, None, None

    monkeypatch.setattr(transcription_api.librosa, "pyin", no_pitch_frames)

    assert transcribe_bass_stem(stem_data) == []


def test_energy_mask_handles_empty_and_short_rms(monkeypatch) -> None:
    """Cover silence and padding branches in the frame energy mask."""

    def empty_rms(*_args, **_kwargs):
        """Return no RMS frames."""
        return np.array([[]])

    monkeypatch.setattr(transcription_api.librosa.feature, "rms", empty_rms)
    assert not transcription_api._energy_mask(np.array([], dtype=np.float32), 3).any()

    def one_frame_rms(*_args, **_kwargs):
        """Return fewer RMS frames than pYIN produced."""
        return np.array([[1.0]])

    monkeypatch.setattr(transcription_api.librosa.feature, "rms", one_frame_rms)
    mask = transcription_api._energy_mask(np.ones(8, dtype=np.float32), 3)
    assert mask.tolist() == [True, False, False]


def test_note_events_from_frames_skips_invalid_and_short_regions() -> None:
    """Skip unvoiced and too-short pYIN regions instead of emitting noise."""
    assert (
        transcription_api._note_events_from_frames(
            np.array([np.nan], dtype=np.float64),
            np.array([True]),
            SAMPLE_RATE,
        )
        == []
    )
    assert (
        transcription_api._note_events_from_frames(
            np.array([82.406889], dtype=np.float64),
            np.array([True]),
            SAMPLE_RATE,
        )
        == []
    )


def test_merge_adjacent_equal_pitches() -> None:
    """Merge note fragments when pYIN briefly drops voicing."""
    events = transcription_api._merge_adjacent_equal_pitches(
        [
            NoteEvent("E2", 0.0, 0.2),
            NoteEvent("E2", 0.25, 0.2),
            NoteEvent("A2", 0.8, 0.2),
        ]
    )

    assert events == [
        NoteEvent("E2", 0.0, 0.45),
        NoteEvent("A2", 0.8, 0.2),
    ]


def _render_bass_sequence(notes: list[ExpectedNote]) -> bytes:
    """Render a short monophonic bass line to WAV bytes."""
    pieces: list[np.ndarray] = []
    cursor = 0.0
    for note in notes:
        if note.start_time > cursor:
            pieces.append(np.zeros(int((note.start_time - cursor) * SAMPLE_RATE)))
        pieces.append(_sine_note(_note_hz(note.pitch), note.duration))
        cursor = note.start_time + note.duration

    return _wav_bytes(np.concatenate(pieces).astype(np.float32))


def _sine_note(frequency_hz: float, duration: float) -> np.ndarray:
    """Render one note with a short fade to avoid click-induced onsets."""
    sample_count = int(duration * SAMPLE_RATE)
    t = np.arange(sample_count, dtype=np.float64) / SAMPLE_RATE
    audio = 0.35 * np.sin(2 * np.pi * frequency_hz * t)
    fade_count = min(sample_count // 2, int(0.02 * SAMPLE_RATE))
    if fade_count > 0:
        fade = np.linspace(0.0, 1.0, fade_count)
        audio[:fade_count] *= fade
        audio[-fade_count:] *= fade[::-1]
    return audio.astype(np.float32)


def _wav_bytes(audio: np.ndarray) -> bytes:
    """Serialize mono float audio to WAV bytes."""
    buffer = io.BytesIO()
    sf.write(buffer, audio, SAMPLE_RATE, format="WAV")
    return buffer.getvalue()


def _note_hz(note: str) -> float:
    """Return equal-tempered frequency for the notes used by tests."""
    frequencies = {
        "E2": 82.406889,
        "A2": 110.0,
        "D3": 146.832384,
    }
    return frequencies[note]


def _onset_pitch_f1(
    detected: list[NoteEvent],
    expected: list[ExpectedNote],
    onset_tolerance: float = 0.12,
) -> float:
    """Compute pitch/onset F1 by greedily matching expected notes once."""
    matched_expected: set[int] = set()
    true_positives = 0
    for event in detected:
        for index, expected_event in enumerate(expected):
            if index in matched_expected:
                continue
            if event.pitch != expected_event.pitch:
                continue
            if abs(event.start_time - expected_event.start_time) > onset_tolerance:
                continue
            matched_expected.add(index)
            true_positives += 1
            break

    false_positives = len(detected) - true_positives
    false_negatives = len(expected) - true_positives
    denominator = 2 * true_positives + false_positives + false_negatives
    if denominator == 0:
        return 1.0
    return 2 * true_positives / denominator
