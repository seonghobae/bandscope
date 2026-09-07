"""Transcription API endpoints."""

from __future__ import annotations

import io
from dataclasses import dataclass

import librosa
import numpy as np
from numpy.typing import NDArray

from bandscope_analysis.audio_decode import decode_mono_audio
from bandscope_analysis.audio_resource_policy import AudioResourcePolicy

TARGET_SR = 22050
MAX_STEM_BYTES = 50 * 1024 * 1024
MAX_TRANSCRIPTION_DURATION_SECONDS = 120
FRAME_LENGTH = 2048
HOP_LENGTH = 512
MIN_NOTE_DURATION_SECONDS = 0.05
MIN_SIGNAL_PEAK = 1e-5
TRANSCRIPTION_RESOURCE_POLICY = AudioResourcePolicy(
    max_encoded_file_bytes=MAX_STEM_BYTES,
    target_sample_rate=TARGET_SR,
    max_duration_seconds=MAX_TRANSCRIPTION_DURATION_SECONDS,
    max_decoded_audio_bytes=(TARGET_SR * MAX_TRANSCRIPTION_DURATION_SECONDS + 1) * 8,
)


@dataclass
class NoteEvent:
    """Represents a transcribed musical note."""

    pitch: str
    start_time: float
    duration: float


def transcribe_bass_stem(stem_data: bytes) -> list[NoteEvent]:
    """Transcribe a bass stem into note events using local pitch tracking.

    Args:
        stem_data: Binary data representing the audio stem.

    Returns:
        Note events containing pitch, start time, and duration.
    """
    if not stem_data:
        return []
    if len(stem_data) > MAX_STEM_BYTES:
        raise ValueError("Stem data is too large for transcription.")

    source = io.BytesIO(stem_data)
    y_array, sr = decode_mono_audio(source, policy=TRANSCRIPTION_RESOURCE_POLICY)
    if y_array.size == 0 or float(np.max(np.abs(y_array))) < MIN_SIGNAL_PEAK:
        return []

    fmin = float(librosa.note_to_hz("C1"))
    fmax = float(librosa.note_to_hz("C5"))
    try:
        f0, voiced_flag, _voiced_probs = librosa.pyin(
            y_array,
            fmin=fmin,
            fmax=fmax,
            sr=sr,
            frame_length=FRAME_LENGTH,
            hop_length=HOP_LENGTH,
        )
    except librosa.util.exceptions.ParameterError as error:
        raise ValueError(f"Pitch tracking failed: {error}") from error

    if f0 is None or voiced_flag is None:
        return []

    f0_array = np.asarray(f0, dtype=np.float64)
    voiced_frames = np.asarray(voiced_flag, dtype=bool) & np.isfinite(f0_array)
    voiced_frames &= _energy_mask(y_array, len(f0_array))
    return _note_events_from_frames(f0_array, voiced_frames, int(sr))


def _energy_mask(y: NDArray[np.float32], frame_count: int) -> NDArray[np.bool_]:
    """Return frame-level mask that removes silence and decoder padding."""
    rms = librosa.feature.rms(
        y=y,
        frame_length=FRAME_LENGTH,
        hop_length=HOP_LENGTH,
        center=True,
    )[0]
    rms_array = np.asarray(rms, dtype=np.float64)
    if rms_array.size == 0:
        return np.zeros(frame_count, dtype=bool)

    threshold = max(float(np.max(rms_array)) * 0.08, 1e-4)
    mask = rms_array >= threshold
    if mask.size >= frame_count:
        return mask[:frame_count]

    padded = np.zeros(frame_count, dtype=bool)
    padded[: mask.size] = mask
    return padded


def _note_events_from_frames(
    f0: NDArray[np.float64],
    voiced_frames: NDArray[np.bool_],
    sr: int,
) -> list[NoteEvent]:
    """Convert voiced pYIN frames into contiguous note events."""
    frame_times = librosa.frames_to_time(
        np.arange(f0.size + 1),
        sr=sr,
        hop_length=HOP_LENGTH,
    )
    events: list[NoteEvent] = []

    for start_frame, end_frame in _contiguous_regions(voiced_frames):
        frequency_slice = f0[start_frame : end_frame + 1]
        frequency_slice = frequency_slice[np.isfinite(frequency_slice)]
        if frequency_slice.size == 0:
            continue

        start_time = float(frame_times[start_frame])
        end_time = float(frame_times[min(end_frame + 1, frame_times.size - 1)])
        duration = end_time - start_time
        if duration < MIN_NOTE_DURATION_SECONDS:
            continue

        median_hz = float(np.median(frequency_slice))
        pitch = str(librosa.hz_to_note(median_hz, unicode=False))
        events.append(
            NoteEvent(
                pitch=pitch,
                start_time=start_time,
                duration=duration,
            )
        )

    return _merge_adjacent_equal_pitches(events)


def _contiguous_regions(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Return inclusive frame ranges for true regions in a boolean mask."""
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_voiced in enumerate(mask):
        if bool(is_voiced) and start is None:
            start = index
        elif not bool(is_voiced) and start is not None:
            regions.append((start, index - 1))
            start = None
    if start is not None:
        regions.append((start, len(mask) - 1))
    return regions


def _merge_adjacent_equal_pitches(events: list[NoteEvent]) -> list[NoteEvent]:
    """Merge short pitch-equivalent fragments split by frame-level voicing gaps."""
    merged: list[NoteEvent] = []
    for event in events:
        if not merged:
            merged.append(event)
            continue

        previous = merged[-1]
        gap = event.start_time - (previous.start_time + previous.duration)
        if previous.pitch == event.pitch and gap <= 0.08:
            merged[-1] = NoteEvent(
                pitch=previous.pitch,
                start_time=previous.start_time,
                duration=event.start_time + event.duration - previous.start_time,
            )
        else:
            merged.append(event)
    return merged