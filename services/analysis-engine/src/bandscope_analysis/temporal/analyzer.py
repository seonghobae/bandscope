"""Temporal analyzer implementation for audio ingestion and beat tracking."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from numpy.typing import NDArray

from bandscope_analysis.audio_decode import decode_mono_audio
from bandscope_analysis.audio_resource_policy import (
    DEFAULT_AUDIO_RESOURCE_POLICY,
    DEFAULT_MAX_DURATION_SECONDS,
    AudioResourcePolicy,
)

from .model import TemporalFeatures

logger = logging.getLogger(__name__)

# Compatibility aliases retained for callers/tests while the canonical values
# are owned by AudioResourcePolicy. The decode-duration alias intentionally
# includes one rejection-probe sample so an overlong source is detected rather
# than silently truncated at the accepted rehearsal duration.
TARGET_SR = DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate
MAX_AUDIO_FILE_BYTES = DEFAULT_AUDIO_RESOURCE_POLICY.max_encoded_file_bytes
MAX_ANALYSIS_DURATION_SECONDS = DEFAULT_AUDIO_RESOURCE_POLICY.decode_probe_duration_seconds
KNOWN_LIBROSA_NUMBA_WARNING_FILTERS = (
    (DeprecationWarning, r".*pkg_resources is deprecated.*", r".*librosa.*"),
    (FutureWarning, r".*Numba.*", r".*numba.*"),
)
_SAFE_TEMPORAL_FAILURE_MESSAGES = frozenset(
    {
        "Audio file is too large for temporal analysis",
        "Audio input violates the audio resource policy.",
    }
)
_MISSING_AUDIO_MESSAGE = "Audio source is unavailable for temporal analysis."
_GENERIC_TEMPORAL_FAILURE_MESSAGE = "Temporal analysis failed."
# ponytail: assumes 4/4; upgrade to meter estimation or a madmom DBN if other meters matter.
BEATS_PER_BAR = 4


def _estimate_downbeats(
    onset_env: NDArray[np.floating[Any]],
    beat_frames: NDArray[np.integer[Any]],
    beat_times: NDArray[np.floating[Any]],
    beats_per_bar: int = BEATS_PER_BAR,
) -> list[float]:
    """Pick the bar phase whose beats carry the most onset energy as the downbeats.

    Downbeats are typically the strongest onset in a bar, so instead of blindly
    treating beat 0 as the downbeat we sample the onset-strength envelope at each
    beat and choose the phase (0..beats_per_bar-1) with the highest mean strength.
    This looks at the actual audio rather than assuming beat 0 starts the bar.
    """
    if len(beat_times) == 0:
        return []
    if len(beat_times) < beats_per_bar or len(onset_env) == 0:
        return [float(beat_times[0])]
    idx = np.clip(beat_frames, 0, len(onset_env) - 1)
    beat_strength = onset_env[idx]
    best_phase, best_score = 0, -np.inf
    for phase in range(beats_per_bar):
        window = beat_strength[phase::beats_per_bar]
        score = float(np.mean(window)) if len(window) else -np.inf
        if score > best_score:
            best_score, best_phase = score, phase
    return [float(bt) for i, bt in enumerate(beat_times) if (i - best_phase) % beats_per_bar == 0]


def _safe_temporal_failure_message(error: Exception) -> str:
    """Return an allowlisted diagnostic without relaying decoder payload text."""
    message = str(error)
    if message in _SAFE_TEMPORAL_FAILURE_MESSAGES:
        return message
    return _GENERIC_TEMPORAL_FAILURE_MESSAGE


class TemporalAnalyzer:
    """Analyze bounded temporal features (BPM and beat grids) from local audio."""

    def __init__(self, resource_policy: AudioResourcePolicy | None = None) -> None:
        """Create an analyzer bound to one canonical local-audio resource policy.

        Args:
            resource_policy: Explicit policy for tests or specialized callers.
                The default preserves the public module-level byte ceiling while
                taking sample-rate and accepted rehearsal duration from the
                canonical policy layer.
        """
        self.resource_policy = resource_policy or AudioResourcePolicy(
            max_encoded_file_bytes=MAX_AUDIO_FILE_BYTES,
            target_sample_rate=TARGET_SR,
            max_duration_seconds=DEFAULT_MAX_DURATION_SECONDS,
        )

    def analyze(self, audio_path: str | Path) -> TemporalFeatures:
        """Decode bounded audio and extract temporal features.

        Args:
            audio_path: Path to the audio file.

        Returns:
            TemporalFeatures containing BPM and beat grids.
        """
        path = Path(audio_path)
        path_str = str(path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(_MISSING_AUDIO_MESSAGE)

        logger.info("Loading and decoding bounded local audio.")

        try:
            with path.open("rb") as fileobj:
                file_size = os.fstat(fileobj.fileno()).st_size
                try:
                    self.resource_policy.validate_encoded_file_bytes(file_size)
                except ValueError as error:
                    raise ValueError("Audio file is too large for temporal analysis") from error
                y_array, sr = decode_mono_audio(fileobj, policy=self.resource_policy)

            duration = float(librosa.get_duration(y=y_array, sr=sr))

            logger.info("Extracting tempo and beat tracking...")
            tempo, beat_frames = librosa.beat.beat_track(y=y_array, sr=sr)
            beat_times: NDArray[np.floating[Any]] = librosa.frames_to_time(beat_frames, sr=sr)

            # Place downbeats on the strongest-onset bar phase (looks at the audio,
            # not a blind "every 4th beat from index 0").
            onset_env = librosa.onset.onset_strength(y=y_array, sr=sr)
            downbeat_times = _estimate_downbeats(onset_env, beat_frames, beat_times)

            bpm_val = float(tempo[0]) if isinstance(tempo, np.ndarray) else float(tempo)

            logger.info(f"Analysis complete: {bpm_val:.1f} BPM, {len(beat_times)} beats detected.")

            return {
                "bpm": bpm_val,
                "beat_times": [float(bt) for bt in beat_times],
                "downbeat_times": downbeat_times,
                "duration_seconds": duration,
                "sample_rate": int(sr),
                "audio_path": path_str,
            }

        except Exception as error:
            logger.error("Temporal analysis failed (%s).", type(error).__name__)
            raise ValueError(_safe_temporal_failure_message(error)) from error