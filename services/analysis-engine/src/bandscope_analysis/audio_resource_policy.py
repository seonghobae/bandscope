"""Canonical resource admission policy for local audio analysis.

The policy is intentionally independent of individual analyzers. Expensive
feature code consumes a decoded artifact only after encoded-file and decoded
output checks agree on the same versioned limits. This prevents temporal,
separation, chord, and register features from silently inventing incompatible
resource ceilings.

Security Notes:
- Encoded byte counts are validated before decode/allocation work when the
  opened file descriptor can provide an authoritative size.
- Decoded audio is revalidated because container metadata and decoder behavior
  are untrusted; accepted artifacts are finite, mono, floating-point, at the
  configured sample rate, and within configured sample and memory budgets.
- Decoders receive a one-sample-over-budget probe duration so a longer source is
  rejected instead of being silently truncated to the accepted duration.
- Policy arithmetic rejects unrepresentable limits before float/sample-count
  conversion so malformed configuration cannot escape the stable failure mode.
- Resource rejections expose only a stable reason and policy version; messages
  remain payload-free and never include source paths or audio content.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any, NoReturn, cast

import numpy as np
from numpy.typing import NDArray

AUDIO_RESOURCE_POLICY_VERSION = "1"
DEFAULT_TARGET_SAMPLE_RATE = 44_100
DEFAULT_MIN_SOURCE_SAMPLE_RATE = 8_000
DEFAULT_MAX_SOURCE_SAMPLE_RATE = 192_000
DEFAULT_MIN_SOURCE_CHANNELS = 1
DEFAULT_MAX_SOURCE_CHANNELS = 2
DEFAULT_MAX_ENCODED_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS = 15 * 60
DEFAULT_MAX_DECODED_AUDIO_BYTES = (
    DEFAULT_TARGET_SAMPLE_RATE * DEFAULT_MAX_DURATION_SECONDS * np.dtype(np.float64).itemsize
)
_POLICY_ERROR = "Audio input violates the audio resource policy."


class AudioResourcePolicyError(ValueError):
    """Payload-free resource rejection with stable machine-readable provenance."""

    def __init__(self, reason: str) -> None:
        """Record a stable rejection reason and the policy version that produced it."""
        super().__init__(_POLICY_ERROR)
        self.reason = reason
        self.policy_version = AUDIO_RESOURCE_POLICY_VERSION


def _reject(reason: str) -> NoReturn:
    """Fail closed without echoing untrusted resource metadata."""
    raise AudioResourcePolicyError(reason)


@dataclass(frozen=True)
class AudioResourcePolicy:
    """Versioned limits applied before and after local audio decoding.

    Args:
        max_encoded_file_bytes: Maximum non-empty encoded source size.
        target_sample_rate: Required sample rate of the canonical decoded mono
            artifact.
        max_duration_seconds: Maximum decoded duration represented as a sample
            ceiling at ``target_sample_rate``.
        max_decoded_audio_bytes: Maximum in-memory byte size of the canonical
            decoded mono NumPy buffer.
        min_source_sample_rate: Minimum source-container sample rate accepted
            before resampling.
        max_source_sample_rate: Maximum source-container sample rate accepted
            before resampling.
        min_source_channels: Minimum source-container channel count accepted
            before downmixing.
        max_source_channels: Maximum source-container channel count accepted
            before downmixing.
    """

    max_encoded_file_bytes: int = DEFAULT_MAX_ENCODED_FILE_BYTES
    target_sample_rate: int = DEFAULT_TARGET_SAMPLE_RATE
    max_duration_seconds: float = float(DEFAULT_MAX_DURATION_SECONDS)
    max_decoded_audio_bytes: int = DEFAULT_MAX_DECODED_AUDIO_BYTES
    min_source_sample_rate: int = DEFAULT_MIN_SOURCE_SAMPLE_RATE
    max_source_sample_rate: int = DEFAULT_MAX_SOURCE_SAMPLE_RATE
    min_source_channels: int = DEFAULT_MIN_SOURCE_CHANNELS
    max_source_channels: int = DEFAULT_MAX_SOURCE_CHANNELS

    def __post_init__(self) -> None:
        """Reject invalid policy configuration before it can weaken admission."""
        if (
            isinstance(self.max_encoded_file_bytes, bool)
            or not isinstance(self.max_encoded_file_bytes, int)
            or self.max_encoded_file_bytes <= 0
            or self.max_encoded_file_bytes > sys.maxsize - 1
        ):
            raise ValueError(_POLICY_ERROR)
        if (
            isinstance(self.target_sample_rate, bool)
            or not isinstance(self.target_sample_rate, int)
            or self.target_sample_rate <= 0
            or self.target_sample_rate > sys.maxsize - 1
        ):
            raise ValueError(_POLICY_ERROR)
        if isinstance(self.max_duration_seconds, bool) or not isinstance(
            self.max_duration_seconds, int | float
        ):
            raise ValueError(_POLICY_ERROR)
        if (
            isinstance(self.max_decoded_audio_bytes, bool)
            or not isinstance(self.max_decoded_audio_bytes, int)
            or self.max_decoded_audio_bytes <= 0
            or self.max_decoded_audio_bytes > sys.maxsize - 1
        ):
            raise ValueError(_POLICY_ERROR)
        for source_bound in (
            self.min_source_sample_rate,
            self.max_source_sample_rate,
            self.min_source_channels,
            self.max_source_channels,
        ):
            if (
                isinstance(source_bound, bool)
                or not isinstance(source_bound, int)
                or source_bound <= 0
                or source_bound > sys.maxsize - 1
            ):
                raise ValueError(_POLICY_ERROR)
        if (
            self.min_source_sample_rate > self.max_source_sample_rate
            or self.min_source_channels > self.max_source_channels
        ):
            raise ValueError(_POLICY_ERROR)
        try:
            duration_seconds = float(self.max_duration_seconds)
        except (OverflowError, ValueError):
            raise ValueError(_POLICY_ERROR) from None
        if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
            raise ValueError(_POLICY_ERROR)
        decoded_samples = self.target_sample_rate * duration_seconds
        if (
            not math.isfinite(decoded_samples)
            or decoded_samples < 1.0
            or decoded_samples > sys.maxsize - 1
        ):
            raise ValueError(_POLICY_ERROR)

    @property
    def max_decoded_samples(self) -> int:
        """Return the maximum mono sample count allowed after decoding."""
        return int(self.target_sample_rate * float(self.max_duration_seconds))

    @property
    def decode_probe_duration_seconds(self) -> float:
        """Return a bounded decoder duration that includes one rejection probe sample."""
        return (self.max_decoded_samples + 1) / self.target_sample_rate

    def validate_encoded_file_bytes(self, file_size: object) -> int:
        """Validate an authoritative encoded file size before decoding.

        Args:
            file_size: Byte count obtained from the already-open source file.

        Returns:
            The validated integer byte count.

        Raises:
            AudioResourcePolicyError: If the value is not a positive integer
                within policy.
        """
        if isinstance(file_size, bool) or not isinstance(file_size, int) or file_size <= 0:
            _reject("malformed_header")
        if file_size > self.max_encoded_file_bytes:
            _reject("encoded_file_too_large")
        return file_size

    def validate_source_metadata(
        self,
        frames: object,
        sample_rate: object,
        channels: object,
    ) -> None:
        """Validate source-container metadata before any decode transformation.

        Args:
            frames: Number of source frames reported by the container parser.
            sample_rate: Source sample rate in Hz before resampling.
            channels: Source channel count before downmixing.

        Raises:
            AudioResourcePolicyError: If metadata is malformed or outside the
                source bounds.
        """
        if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
            _reject("malformed_header")
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate < self.min_source_sample_rate
            or sample_rate > self.max_source_sample_rate
        ):
            _reject("sampling_rate_unsupported")
        if (
            isinstance(channels, bool)
            or not isinstance(channels, int)
            or channels < self.min_source_channels
            or channels > self.max_source_channels
        ):
            _reject("channel_count_unsupported")
        try:
            source_duration_seconds = float(frames) / float(sample_rate)
        except (OverflowError, ValueError):
            _reject("malformed_header")
        if source_duration_seconds > float(self.max_duration_seconds):
            _reject("duration_exceeded")

    def validate_decoded_audio(
        self,
        audio: object,
        sample_rate: object,
    ) -> NDArray[np.floating[Any]]:
        """Revalidate the canonical decoded artifact before feature analysis.

        Args:
            audio: Candidate mono NumPy array returned by the decoder.
            sample_rate: Decoder-reported sample rate in Hz.

        Returns:
            The original validated NumPy floating-point array without copying it.

        Raises:
            AudioResourcePolicyError: If dtype, shape, sample rate, sample
                count, memory use, or finiteness does not satisfy this policy.
        """
        if (
            not isinstance(audio, np.ndarray)
            or audio.ndim != 1
            or audio.size == 0
            or not np.issubdtype(audio.dtype, np.floating)
        ):
            _reject("malformed_header")
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate != self.target_sample_rate
        ):
            _reject("sampling_rate_unsupported")
        if audio.size > self.max_decoded_samples:
            _reject("decoded_sample_count_exceeded")
        if audio.nbytes > self.max_decoded_audio_bytes:
            _reject("memory_budget_exceeded")
        if not np.isfinite(audio).all():
            _reject("malformed_header")
        return cast(NDArray[np.floating[Any]], audio)


DEFAULT_AUDIO_RESOURCE_POLICY = AudioResourcePolicy()

__all__ = [
    "AUDIO_RESOURCE_POLICY_VERSION",
    "AudioResourcePolicy",
    "AudioResourcePolicyError",
    "DEFAULT_AUDIO_RESOURCE_POLICY",
    "DEFAULT_MAX_DECODED_AUDIO_BYTES",
    "DEFAULT_MAX_DURATION_SECONDS",
    "DEFAULT_MAX_ENCODED_FILE_BYTES",
    "DEFAULT_MAX_SOURCE_CHANNELS",
    "DEFAULT_MAX_SOURCE_SAMPLE_RATE",
    "DEFAULT_MIN_SOURCE_CHANNELS",
    "DEFAULT_MIN_SOURCE_SAMPLE_RATE",
    "DEFAULT_TARGET_SAMPLE_RATE",
]
