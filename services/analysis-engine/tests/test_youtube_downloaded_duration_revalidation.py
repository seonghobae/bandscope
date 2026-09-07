"""Post-download YouTube duration revalidation regressions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from bandscope_analysis.youtube import download_youtube_audio


@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.os.path.isfile")
@patch("bandscope_analysis.youtube.os.remove")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_youtube_revalidates_downloaded_duration_before_returning_success(
    mock_ydl_class: MagicMock,
    mock_remove: MagicMock,
    mock_isfile: MagicMock,
    mock_exists: MagicMock,
) -> None:
    """Changed download metadata must not bypass the 15-minute admission limit."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    out_dir = str(Path("/tmp").resolve())
    mock_ydl.extract_info.side_effect = [
        {"id": "abc123DEF45", "duration": 60},
        {"id": "abc123DEF45", "title": "Changed metadata", "duration": 16 * 60},
    ]
    mock_ydl.prepare_filename.return_value = f"{out_dir}/abc123DEF45.m4a"
    mock_exists.return_value = True
    mock_isfile.return_value = True

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", out_dir)

    assert result == {
        "ok": False,
        "error": {
            "code": "duration_exceeded",
            "message": "Video exceeds the 15-minute limit.",
        },
    }
    mock_remove.assert_called_once_with(f"{out_dir}/abc123DEF45.m4a")
