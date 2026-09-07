"""Regression tests for fail-closed source-separation model output."""

from __future__ import annotations

import numpy as np
import pytest

from bandscope_analysis.separation.audio_separator import _as_float_array


@pytest.mark.parametrize(
    "values",
    [
        np.array([], dtype=np.float32),
        np.array([np.nan], dtype=np.float32),
        np.array([np.inf], dtype=np.float32),
        np.array([np.finfo(np.float64).max], dtype=np.float64),
    ],
)
def test_model_output_rejects_empty_nonfinite_or_float32_overflow(values: np.ndarray) -> None:
    """Malformed model stems must fail instead of becoming successful silence."""
    with pytest.raises(ValueError, match=r"^Stem separation produced invalid audio\.$"):
        _as_float_array(values)


def test_model_output_wraps_non_numeric_conversion_errors() -> None:
    """Non-numeric model output must fail with the stable payload-free error."""
    with pytest.raises(ValueError, match=r"^Stem separation produced invalid audio\.$"):
        _as_float_array(object())


def test_model_output_preserves_valid_finite_samples() -> None:
    """Valid model samples remain finite float32 audio with their original values."""
    values = np.array([0.25, -0.5, 0.75], dtype=np.float64)

    result = _as_float_array(values)

    assert result.dtype == np.float32
    assert np.array_equal(result, values.astype(np.float32))
