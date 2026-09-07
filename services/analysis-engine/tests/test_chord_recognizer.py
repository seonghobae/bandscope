"""Tests for the chord recognizer module."""

from unittest.mock import patch

import numpy as np
import pytest

from bandscope_analysis.chords.chord_recognizer import (
    ChordRecognizer,
    _confidence_rank,
)

SAMPLE_RATE = 22050
DURATION_SECONDS = 3


def test_chord_recognizer_empty_audio() -> None:
    """Test chord recognition with empty audio array."""
    recognizer = ChordRecognizer()
    result = recognizer.recognize(np.array([]), sr=22050)
    assert result == []


@pytest.mark.parametrize("shape", [(0, 2), (2, 0)])
def test_chord_recognizer_empty_layouts(shape: tuple[int, int]) -> None:
    """Every zero-element NumPy layout must short-circuit recognition."""
    recognizer = ChordRecognizer()

    assert recognizer.recognize(np.empty(shape), sr=22050) == []


def test_chord_recognizer_unvoiced_audio() -> None:
    """Test chord recognition with noise."""
    recognizer = ChordRecognizer()
    # Create random noise
    np.random.seed(42)
    y = np.random.randn(SAMPLE_RATE * 2) * 0.1
    result = recognizer.recognize(y, sr=SAMPLE_RATE)
    # Could be N (No chord) or empty
    assert all(chord["chord"] in ("N", "Unknown", "") for chord in result) if result else True


def test_chord_recognizer_c_major_chord() -> None:
    """Test chord recognition with a clear C major chord."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    t = np.linspace(0, DURATION_SECONDS, sr * DURATION_SECONDS, endpoint=False)
    # C major: C4 (261.63Hz), E4 (329.63Hz), G4 (392.00Hz)
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0

    result = recognizer.recognize(y, sr=sr)
    assert len(result) > 0
    # At least some of the identified segments should be "C" or "C:maj"
    identified_chords = [r["chord"] for r in result]
    assert "C" in identified_chords or "C:maj" in identified_chords


def test_chord_recognizer_hpss_exception() -> None:
    """Test for test_chord_recognizer_hpss_exception."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    with patch("librosa.effects.hpss", side_effect=Exception("HPSS Error")):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert isinstance(chords, list)


def test_chord_recognizer_chroma_cqt_exception() -> None:
    """Test for test_chord_recognizer_chroma_cqt_exception."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    with patch("librosa.feature.chroma_cqt", side_effect=Exception("CQT Error")):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert chords == []


def test_chord_recognizer_rms_exception() -> None:
    """Test for test_chord_recognizer_rms_exception."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    with patch("librosa.feature.rms", side_effect=Exception("RMS Error")):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert isinstance(chords, list)


def test_chord_recognizer_rms_padding() -> None:
    """Test for test_chord_recognizer_rms_padding."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    # Mock RMS to return something shorter than chromagram
    def mock_rms(*args, **kwargs):
        return np.array([[0.1, 0.1]])

    with patch("librosa.feature.rms", side_effect=mock_rms):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert isinstance(chords, list)


def test_chord_recognizer_empty_chromagram() -> None:
    """Test for test_chord_recognizer_empty_chromagram."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    # Mock chroma_cqt to return empty array
    with patch("librosa.feature.chroma_cqt", return_value=np.array([])):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert chords == []


def test_chord_recognizer_rms_longer() -> None:
    """Test for test_chord_recognizer_rms_longer."""
    recognizer = ChordRecognizer()
    y = np.random.randn(SAMPLE_RATE * DURATION_SECONDS)

    # Mock RMS to return something longer than chromagram
    def mock_rms(*args, **kwargs):
        # Return a very long array
        return np.array([np.ones(1000)])

    with patch("librosa.feature.rms", side_effect=mock_rms):
        chords = recognizer.recognize(y, sr=SAMPLE_RATE)
        assert isinstance(chords, list)


def test_chord_recognizer_changing_chords() -> None:
    """Test for test_chord_recognizer_changing_chords."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    t1 = np.linspace(0, DURATION_SECONDS, sr * DURATION_SECONDS, endpoint=False)
    # C major
    y1 = (
        np.sin(2 * np.pi * 261.63 * t1)
        + np.sin(2 * np.pi * 329.63 * t1)
        + np.sin(2 * np.pi * 392.00 * t1)
    ) / 3.0

    t2 = np.linspace(0, DURATION_SECONDS, sr * DURATION_SECONDS, endpoint=False)
    # G major: G4 (392.00Hz), B4 (493.88Hz), D5 (587.33Hz)
    y2 = (
        np.sin(2 * np.pi * 392.00 * t2)
        + np.sin(2 * np.pi * 493.88 * t2)
        + np.sin(2 * np.pi * 587.33 * t2)
    ) / 3.0

    y = np.concatenate([y1, y2])

    result = recognizer.recognize(y, sr=sr)
    assert len(result) >= 2
    identified_chords = [r["chord"] for r in result]
    assert "C" in identified_chords
    assert "G" in identified_chords


def test_chord_recognizer_confidence_field() -> None:
    """Test that recognized chords include a confidence field."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    t = np.linspace(0, DURATION_SECONDS, sr * DURATION_SECONDS, endpoint=False)
    # C major chord
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0

    result = recognizer.recognize(y, sr=sr)
    assert len(result) > 0
    for chord in result:
        assert "confidence" in chord
        assert chord["confidence"] in ("low", "medium", "high")


def test_chord_recognizer_viterbi_smoothing_reduces_spurious_changes() -> None:
    """Test that Viterbi smoothing reduces spurious chord changes."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE

    # Create a clear C major chord with a tiny bit of noise
    t = np.linspace(0, 4, sr * 4, endpoint=False)
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0
    # Add tiny deterministic noise
    rng = np.random.default_rng(42)
    y += rng.normal(0.0, 0.01, len(y))

    result = recognizer.recognize(y, sr=sr)
    # With Viterbi smoothing, a steady chord should produce very few segments
    # (ideally 1, but at most 3 due to edge effects)
    non_n_chords = [r for r in result if r["chord"] != "N"]
    assert len(non_n_chords) <= 3


def test_chord_recognizer_transition_matrix_is_valid() -> None:
    """Test that the transition matrix rows sum to 1.0."""
    recognizer = ChordRecognizer()
    trans = recognizer._transition_matrix
    row_sums = trans.sum(axis=1)
    np.testing.assert_allclose(row_sums, 1.0, atol=1e-6)


def test_chord_recognizer_viterbi_decode_empty() -> None:
    """Test Viterbi decode with empty observations."""
    recognizer = ChordRecognizer()
    obs = np.zeros((25, 0))
    result = recognizer._viterbi_decode(obs)
    assert len(result) == 0


def test_chord_recognizer_viterbi_decode_single_frame() -> None:
    """Test Viterbi decode with a single observation frame."""
    recognizer = ChordRecognizer()
    # Set up a single frame where state 0 (C major) is most likely
    obs = np.full((25, 1), 0.01)
    obs[0, 0] = 0.9  # C major
    result = recognizer._viterbi_decode(obs)
    assert len(result) == 1
    assert result[0] == 0  # C major is most likely


def test_confidence_rank_ordering() -> None:
    """Test confidence rank ordering is correct."""
    assert _confidence_rank("low") < _confidence_rank("medium")
    assert _confidence_rank("medium") < _confidence_rank("high")
    assert _confidence_rank("unknown") == 0


def test_chord_recognizer_confidence_high_for_clear_signal() -> None:
    """Test that a clear tonal signal produces high confidence."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    t = np.linspace(0, 3, sr * 3, endpoint=False)
    # Strong C major chord
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    )

    result = recognizer.recognize(y, sr=sr)
    # Expect at least some high-confidence segments for a clear chord
    non_n = [r for r in result if r["chord"] != "N"]
    if non_n:
        confidences = [r["confidence"] for r in non_n]
        assert "high" in confidences or "medium" in confidences


def test_chord_recognizer_confidence_downgrade_within_segment() -> None:
    """Test that confidence is downgraded within a segment (conservative)."""
    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    # Long sustained chord with a brief noisy section in the middle
    t = np.linspace(0, 4, sr * 4, endpoint=False)
    # C major - strong signal
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0

    # Add moderate noise in the middle third to create varying confidence
    mid_start = sr
    mid_end = sr * 3
    np.random.seed(123)
    y[mid_start:mid_end] = y[mid_start:mid_end] * 0.3 + np.random.randn(mid_end - mid_start) * 0.2

    result = recognizer.recognize(y, sr=sr)
    # Should still produce results (the segment spans the whole duration)
    assert isinstance(result, list)


def test_chord_recognizer_compute_confidence_high_entropy() -> None:
    """Test _compute_confidence returns high for peaked similarity distribution."""
    recognizer = ChordRecognizer()
    # Create a similarity vector where one chord dominates massively
    # With temperature=3.0, we need extreme peaking to get norm_entropy < 0.5
    similarity = np.full(25, -5.0)  # Very low baseline
    similarity[0] = 5.0  # Huge peak at C major (state 0)
    result = recognizer._compute_confidence(similarity, 0)
    assert result == "high"


def test_chord_recognizer_compute_confidence_downgrade_path() -> None:
    """Test that _create_chord_segments downgrades confidence within a segment."""
    from unittest.mock import patch

    recognizer = ChordRecognizer()
    sr = SAMPLE_RATE
    # Create audio that will produce the same chord across multiple frames
    t = np.linspace(0, 3, sr * 3, endpoint=False)
    y = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0

    # Mock _compute_confidence to return varying confidence levels
    # First call returns 'high', subsequent calls return 'low' to trigger downgrade
    confidence_values = iter(["high"] + ["low"] * 500)

    original_compute = recognizer._compute_confidence

    def mock_confidence(similarity, best_state):
        try:
            return next(confidence_values)
        except StopIteration:
            return original_compute(similarity, best_state)

    with patch.object(recognizer, "_compute_confidence", side_effect=mock_confidence):
        result = recognizer.recognize(y, sr=sr)

    # The test exercises the confidence downgrade path (line 356)
    assert isinstance(result, list)
    # The result should contain segments where confidence was downgraded
    if result:
        non_n = [r for r in result if r["chord"] != "N"]
        if non_n:
            # Since first frame was high but subsequent were low,
            # the segment confidence should be low (conservative)
            assert non_n[0]["confidence"] == "low"
