"""Fail-closed YouTube duration metadata admission contract."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bandscope_analysis.youtube import download_youtube_audio


class _NonCanonicalFloat(float):
    """Numeric subtype that must not cross the untrusted metadata boundary."""


@pytest.mark.parametrize(
    "duration",
    [
        True,
        0,
        -1,
        float("nan"),
        float("inf"),
        "60",
        object(),
        _NonCanonicalFloat(60.0),
    ],
)
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_youtube_rejects_malformed_announced_duration_before_download(
    mock_ydl_class: MagicMock,
    duration: object,
) -> None:
    """Malformed known-duration metadata must not authorize a media download."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {
        "id": "abc123DEF45",
        "duration": duration,
    }

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result == {
        "ok": False,
        "error": {
            "code": "download_error",
            "message": "YouTube import failed. Please use a local audio file instead.",
        },
    }
    mock_ydl.extract_info.assert_called_once_with(
        "https://youtube.com/watch?v=abc123DEF45",
        download=False,
    )
