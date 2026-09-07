"""Source-container metadata preflight regressions."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bandscope_analysis.audio_metadata import preflight_audio_metadata
from bandscope_analysis.audio_resource_policy import AudioResourcePolicyError


def _info(*, frames: int = 44_100, samplerate: int = 44_100, channels: int = 2) -> SimpleNamespace:
    """Build the metadata subset consumed by the preflight boundary."""
    return SimpleNamespace(frames=frames, samplerate=samplerate, channels=channels)


@patch("bandscope_analysis.audio_metadata.soundfile.info")
def test_preflight_accepts_metadata_and_rewinds_the_caller_handle(mock_info: object) -> None:
    """A successful metadata probe leaves the decoder handle at its beginning."""
    source = io.BytesIO(b"header-bytes")

    def inspect(handle: io.BytesIO) -> SimpleNamespace:
        """Consume a small header before returning parsed metadata."""
        handle.read(3)
        return _info()

    mock_info.side_effect = inspect  # type: ignore[attr-defined]

    preflight_audio_metadata(source)

    assert source.tell() == 0


@pytest.mark.parametrize(
    ("info", "reason"),
    [
        (_info(frames=44_100 * 901), "duration_exceeded"),
        (_info(samplerate=7_999), "sampling_rate_unsupported"),
        (_info(channels=3), "channel_count_unsupported"),
    ],
)
@patch("bandscope_analysis.audio_metadata.soundfile.info")
def test_preflight_rejects_untrusted_source_metadata(
    mock_info: object,
    info: SimpleNamespace,
    reason: str,
) -> None:
    """Source duration, rate, and channel bounds fail before PCM decode."""
    mock_info.return_value = info  # type: ignore[attr-defined]

    with pytest.raises(AudioResourcePolicyError, match="audio resource policy") as error:
        preflight_audio_metadata(io.BytesIO(b"header"))

    assert error.value.reason == reason


@pytest.mark.parametrize(
    "dependency_error",
    [RuntimeError("decoder detail"), ValueError("decoder detail")],
)
def test_preflight_maps_parser_failures_to_payload_free_policy_error(
    dependency_error: Exception,
) -> None:
    """Container parser failures cannot masquerade as policy errors or leak decoder detail."""
    with patch(
        "bandscope_analysis.audio_metadata.soundfile.info",
        side_effect=dependency_error,
    ):
        with pytest.raises(AudioResourcePolicyError, match="audio resource policy") as error:
            preflight_audio_metadata(io.BytesIO(b"bad-header"))

    assert error.value.reason == "malformed_header"
    assert error.value.policy_version == "1"
    assert "decoder detail" not in str(error.value)


@patch("bandscope_analysis.audio_metadata.soundfile.info")
def test_preflight_maps_rewind_failures_to_payload_free_policy_error(mock_info: object) -> None:
    """A handle that cannot rewind after probing cannot reach a decoder."""

    class SeekFailsAfterProbe(io.BytesIO):
        """Fail only when the metadata boundary tries to rewind the handle."""

        def __init__(self) -> None:
            """Initialize the caller-owned byte handle and seek counter."""
            super().__init__(b"header")
            self.seek_count = 0

        def seek(self, *args: object, **kwargs: object) -> int:
            """Reject the second seek, which is the post-probe rewind."""
            self.seek_count += 1
            if self.seek_count == 2:
                raise OSError("rewind failed")
            return super().seek(*args, **kwargs)

    mock_info.return_value = _info()  # type: ignore[attr-defined]

    with pytest.raises(AudioResourcePolicyError, match="audio resource policy") as error:
        preflight_audio_metadata(SeekFailsAfterProbe())

    assert error.value.reason == "malformed_header"
    assert error.value.policy_version == "1"
    assert "rewind failed" not in str(error.value)
