"""Contract tests for the canonical local-audio decode port.

These regressions keep resource admission, decoder failure redaction, and
decoded-output validation behind one owned boundary.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from bandscope_analysis import audio_decode
from bandscope_analysis.audio_resource_policy import (
    DEFAULT_AUDIO_RESOURCE_POLICY,
    AudioResourcePolicy,
    AudioResourcePolicyError,
)


def test_decode_mono_audio_preflights_then_validates_one_owned_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep preflight, one decode, and decoded validation in strict order.

    The decode port must own the sequence so downstream analyzers cannot
    bypass or duplicate resource admission.
    """
    source = io.BytesIO(b"container")
    calls: list[tuple[str, object]] = []
    decoder_output = np.array([[0.25, -0.5]], dtype=np.float64)

    def preflight(candidate: object, policy: object) -> None:
        calls.append(("preflight", candidate))
        assert policy is DEFAULT_AUDIO_RESOURCE_POLICY

    def load(candidate: object, **kwargs: object) -> tuple[np.ndarray, int]:
        calls.append(("decode", candidate))
        assert candidate is source
        assert kwargs == {
            "sr": DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate,
            "mono": True,
            "duration": DEFAULT_AUDIO_RESOURCE_POLICY.decode_probe_duration_seconds,
        }
        return decoder_output, DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate

    def validate(self: AudioResourcePolicy, decoded: object, sample_rate: object) -> np.ndarray:
        calls.append(("validate", decoded))
        assert self is DEFAULT_AUDIO_RESOURCE_POLICY
        assert isinstance(decoded, np.ndarray)
        assert decoded.dtype == np.float32
        assert decoded.shape == (2,)
        assert sample_rate == DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate
        return decoded

    monkeypatch.setattr(audio_decode, "preflight_audio_metadata", preflight)
    monkeypatch.setattr(audio_decode.librosa, "load", load)
    monkeypatch.setattr(AudioResourcePolicy, "validate_decoded_audio", validate)

    decoded, sample_rate = audio_decode.decode_mono_audio(
        source,
        policy=DEFAULT_AUDIO_RESOURCE_POLICY,
    )

    assert calls[0] == ("preflight", source)
    assert calls[1] == ("decode", source)
    assert calls[2][0] == "validate"
    np.testing.assert_array_equal(decoded, np.array([0.25, -0.5], dtype=np.float32))
    assert sample_rate == DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate


def test_decode_mono_audio_preserves_resource_policy_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate the canonical preflight rejection without invoking a decoder.

    A rejected source must not consume additional decode resources or lose its typed policy reason.
    """
    rejection = AudioResourcePolicyError("duration_exceeded")

    def reject(_source: object, _policy: object) -> None:
        raise rejection

    monkeypatch.setattr(audio_decode, "preflight_audio_metadata", reject)
    monkeypatch.setattr(
        audio_decode.librosa,
        "load",
        lambda *_args, **_kwargs: pytest.fail("decoder must not run after rejected preflight"),
    )

    with pytest.raises(AudioResourcePolicyError) as caught:
        audio_decode.decode_mono_audio(io.BytesIO(b"container"))

    assert caught.value is rejection


def test_decode_mono_audio_redacts_third_party_decoder_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map third-party decoder details to a payload-safe policy error.

    Native paths or token-shaped details may remain only in the exception
    cause for local debugging, never in buyer-facing error text.
    """
    secret_detail = "/Users/alice/Music/private.m4a token=secret"
    monkeypatch.setattr(audio_decode, "preflight_audio_metadata", lambda *_args: None)
    monkeypatch.setattr(
        audio_decode.librosa,
        "load",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret_detail)),
    )

    with pytest.raises(AudioResourcePolicyError) as caught:
        audio_decode.decode_mono_audio(io.BytesIO(b"container"))

    assert caught.value.reason == "malformed_header"
    assert secret_detail not in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_decode_mono_audio_redacts_malformed_decoder_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject decoder output that cannot be normalized into bounded PCM.

    Malformed third-party values must fail at the decode boundary rather than
    escaping into MIR analyzers.
    """
    monkeypatch.setattr(audio_decode, "preflight_audio_metadata", lambda *_args: None)
    monkeypatch.setattr(
        audio_decode.librosa,
        "load",
        lambda *_args, **_kwargs: ([object()], DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate),
    )

    with pytest.raises(AudioResourcePolicyError) as caught:
        audio_decode.decode_mono_audio(io.BytesIO(b"container"))

    assert caught.value.reason == "malformed_header"


def test_decode_mono_audio_preserves_decoded_policy_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve rejection identity from decoded-audio resource validation.

    The decode port must not collapse a precise post-decode budget failure
    into a generic malformed-container error.
    """
    rejection = AudioResourcePolicyError("decoded_sample_count_exceeded")
    monkeypatch.setattr(audio_decode, "preflight_audio_metadata", lambda *_args: None)
    monkeypatch.setattr(
        audio_decode.librosa,
        "load",
        lambda *_args, **_kwargs: (
            np.array([0.1], dtype=np.float32),
            DEFAULT_AUDIO_RESOURCE_POLICY.target_sample_rate,
        ),
    )

    def reject_decoded(
        _self: AudioResourcePolicy, _decoded: object, _sample_rate: object
    ) -> np.ndarray:
        raise rejection

    monkeypatch.setattr(AudioResourcePolicy, "validate_decoded_audio", reject_decoded)

    with pytest.raises(AudioResourcePolicyError) as caught:
        audio_decode.decode_mono_audio(io.BytesIO(b"container"))

    assert caught.value is rejection
