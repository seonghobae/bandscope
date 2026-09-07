"""Tests for the canonical local-audio resource policy."""

from __future__ import annotations

import numpy as np
import pytest

from bandscope_analysis.audio_resource_policy import (
    AUDIO_RESOURCE_POLICY_VERSION,
    DEFAULT_AUDIO_RESOURCE_POLICY,
    DEFAULT_MAX_SOURCE_CHANNELS,
    DEFAULT_MAX_SOURCE_SAMPLE_RATE,
    DEFAULT_MIN_SOURCE_CHANNELS,
    DEFAULT_MIN_SOURCE_SAMPLE_RATE,
    AudioResourcePolicy,
    AudioResourcePolicyError,
)


def test_default_policy_has_stable_version_and_rehearsal_budget() -> None:
    """The default policy exposes one versioned budget shared by analyzers."""
    assert AUDIO_RESOURCE_POLICY_VERSION == "1"
    assert DEFAULT_AUDIO_RESOURCE_POLICY.max_encoded_file_bytes == 100 * 1024 * 1024
    assert DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate == 44_100
    assert DEFAULT_AUDIO_RESOURCE_POLICY.max_duration_seconds == 15 * 60
    assert DEFAULT_AUDIO_RESOURCE_POLICY.max_decoded_samples == 44_100 * 15 * 60
    assert DEFAULT_AUDIO_RESOURCE_POLICY.max_decoded_audio_bytes == 44_100 * 15 * 60 * 8


def test_oversized_encoded_file_exposes_stable_policy_reason() -> None:
    """Encoded-size rejection carries a stable reason and policy version for UI/provenance."""
    policy = AudioResourcePolicy(max_encoded_file_bytes=100)

    with pytest.raises(AudioResourcePolicyError) as captured:
        policy.validate_encoded_file_bytes(101)

    assert captured.value.reason == "encoded_file_too_large"
    assert captured.value.policy_version == AUDIO_RESOURCE_POLICY_VERSION
    assert "audio resource policy" in str(captured.value).lower()


def test_source_metadata_exposes_stable_policy_reasons() -> None:
    """Container admission distinguishes duration, rate, and channel rejection reasons."""
    policy = AudioResourcePolicy(max_duration_seconds=1.0)

    with pytest.raises(AudioResourcePolicyError) as duration_rejection:
        policy.validate_source_metadata(frames=44_101, sample_rate=44_100, channels=2)
    assert duration_rejection.value.reason == "duration_exceeded"
    assert duration_rejection.value.policy_version == AUDIO_RESOURCE_POLICY_VERSION

    with pytest.raises(AudioResourcePolicyError) as rate_rejection:
        policy.validate_source_metadata(
            frames=44_100,
            sample_rate=DEFAULT_MAX_SOURCE_SAMPLE_RATE + 1,
            channels=2,
        )
    assert rate_rejection.value.reason == "sampling_rate_unsupported"
    assert rate_rejection.value.policy_version == AUDIO_RESOURCE_POLICY_VERSION

    with pytest.raises(AudioResourcePolicyError) as channel_rejection:
        policy.validate_source_metadata(
            frames=44_100,
            sample_rate=44_100,
            channels=DEFAULT_MAX_SOURCE_CHANNELS + 1,
        )
    assert channel_rejection.value.reason == "channel_count_unsupported"
    assert channel_rejection.value.policy_version == AUDIO_RESOURCE_POLICY_VERSION


def test_decoded_memory_rejection_exposes_stable_policy_reason() -> None:
    """Post-decode memory rejection remains machine-readable without exposing payload data."""
    policy = AudioResourcePolicy(
        target_sample_rate=8,
        max_duration_seconds=1.0,
        max_decoded_audio_bytes=16,
    )
    audio = np.zeros(4, dtype=np.float64)

    with pytest.raises(AudioResourcePolicyError) as captured:
        policy.validate_decoded_audio(audio, 8)

    assert captured.value.reason == "memory_budget_exceeded"
    assert captured.value.policy_version == AUDIO_RESOURCE_POLICY_VERSION


@pytest.mark.parametrize("file_size", [True, -1, 0, 101])
def test_encoded_file_size_fails_closed_outside_policy(file_size: object) -> None:
    """Invalid, empty, or oversized encoded inputs are rejected before decode."""
    policy = AudioResourcePolicy(max_encoded_file_bytes=100)

    with pytest.raises(ValueError, match="audio resource policy"):
        policy.validate_encoded_file_bytes(file_size)


def test_encoded_file_size_accepts_exact_boundary() -> None:
    """A non-empty encoded file exactly at the configured ceiling is accepted."""
    policy = AudioResourcePolicy(max_encoded_file_bytes=100)

    assert policy.validate_encoded_file_bytes(100) == 100


def test_source_metadata_accepts_the_published_bounds() -> None:
    """Source metadata accepts the inclusive rate, channel, and duration bounds."""
    policy = AudioResourcePolicy(max_duration_seconds=15 * 60)

    policy.validate_source_metadata(
        frames=DEFAULT_MAX_SOURCE_SAMPLE_RATE * 15 * 60,
        sample_rate=DEFAULT_MAX_SOURCE_SAMPLE_RATE,
        channels=DEFAULT_MAX_SOURCE_CHANNELS,
    )
    policy.validate_source_metadata(
        frames=DEFAULT_MIN_SOURCE_SAMPLE_RATE,
        sample_rate=DEFAULT_MIN_SOURCE_SAMPLE_RATE,
        channels=DEFAULT_MIN_SOURCE_CHANNELS,
    )


@pytest.mark.parametrize(
    ("frames", "sample_rate", "channels"),
    [
        (DEFAULT_MAX_SOURCE_SAMPLE_RATE * (15 * 60 + 1), 44_100, 2),
        (44_100, DEFAULT_MIN_SOURCE_SAMPLE_RATE - 1, 2),
        (44_100, DEFAULT_MAX_SOURCE_SAMPLE_RATE + 1, 2),
        (44_100, 44_100, DEFAULT_MAX_SOURCE_CHANNELS + 1),
        (44_100, 44_100, DEFAULT_MIN_SOURCE_CHANNELS - 1),
        (0, 44_100, 2),
        (44_100, True, 2),
        (44_100, 44_100, True),
        (10**400, 44_100, 2),
    ],
)
def test_source_metadata_fails_closed_before_decode(
    frames: object,
    sample_rate: object,
    channels: object,
) -> None:
    """Overlong and malformed source metadata cannot reach a decoder."""
    with pytest.raises(ValueError, match="audio resource policy"):
        DEFAULT_AUDIO_RESOURCE_POLICY.validate_source_metadata(frames, sample_rate, channels)


@pytest.mark.parametrize(
    ("audio", "sample_rate"),
    [
        (np.zeros(8_001, dtype=np.float32), 8_000),
        (np.zeros((2, 4_000), dtype=np.float32), 8_000),
        (np.array([0.0, np.nan], dtype=np.float32), 8_000),
        (np.array(["not-a-sample"], dtype=object), 8_000),
        (np.zeros(10, dtype=np.int16), 8_000),
        (np.zeros(10, dtype=np.float32), 0),
        (np.zeros(10, dtype=np.float32), True),
    ],
)
def test_decoded_audio_fails_closed_outside_policy(
    audio: np.ndarray,
    sample_rate: object,
) -> None:
    """Decoded output is revalidated for type, shape, finiteness, rate, and sample budget."""
    policy = AudioResourcePolicy(target_sample_rate=8_000, max_duration_seconds=1.0)

    with pytest.raises(ValueError, match="audio resource policy"):
        policy.validate_decoded_audio(audio, sample_rate)


def test_decoded_audio_rejects_buffer_above_memory_budget() -> None:
    """A decoder cannot hide excessive memory behind an allowed sample count."""
    policy = AudioResourcePolicy(
        target_sample_rate=8,
        max_duration_seconds=1.0,
        max_decoded_audio_bytes=16,
    )
    audio = np.zeros(4, dtype=np.float64)

    with pytest.raises(ValueError, match="audio resource policy"):
        policy.validate_decoded_audio(audio, 8)


def test_decoded_audio_accepts_exact_memory_boundary() -> None:
    """A finite canonical buffer exactly at the memory ceiling is accepted."""
    policy = AudioResourcePolicy(
        target_sample_rate=8,
        max_duration_seconds=1.0,
        max_decoded_audio_bytes=32,
    )
    audio = np.zeros(8, dtype=np.float32)

    assert policy.validate_decoded_audio(audio, 8) is audio


def test_decoded_audio_accepts_exact_sample_boundary() -> None:
    """A finite mono artifact exactly at the decoded-sample ceiling is accepted."""
    policy = AudioResourcePolicy(target_sample_rate=8_000, max_duration_seconds=1.0)
    audio = np.zeros(8_000, dtype=np.float32)

    validated = policy.validate_decoded_audio(audio, 8_000)

    assert validated is audio


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_encoded_file_bytes": 0},
        {"target_sample_rate": 0},
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": float("inf")},
        {"max_decoded_audio_bytes": 0},
        {"max_decoded_audio_bytes": True},
        {"min_source_sample_rate": 0},
        {"max_source_channels": True},
        {"min_source_sample_rate": 48_000, "max_source_sample_rate": 44_100},
        {"min_source_channels": 2, "max_source_channels": 1},
    ],
)
def test_policy_configuration_itself_fails_closed(kwargs: dict[str, object]) -> None:
    """Invalid policy construction cannot silently create an unbounded budget."""
    with pytest.raises(ValueError, match="audio resource policy"):
        AudioResourcePolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_sample_rate": 10**400, "max_duration_seconds": 1.0},
        {"target_sample_rate": 1, "max_duration_seconds": 10**400},
        {"max_encoded_file_bytes": 10**400},
        {"max_decoded_audio_bytes": 10**400},
    ],
)
def test_policy_configuration_fails_closed_on_unrepresentable_limits(
    kwargs: dict[str, object],
) -> None:
    """Extreme integer limits cannot escape stable policy validation through overflow."""
    with pytest.raises(ValueError, match="audio resource policy"):
        AudioResourcePolicy(**kwargs)  # type: ignore[arg-type]
