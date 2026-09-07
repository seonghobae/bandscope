"""Regression coverage for post-download YouTube path authority.

The downloader owns only artifacts that resolve beneath the per-import output
directory. Metadata returned by yt-dlp must not turn an arbitrary filesystem path
into a successful import or deletion target.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from bandscope_analysis.audio_resource_policy import DEFAULT_MAX_ENCODED_FILE_BYTES
from bandscope_analysis.youtube import YOUTUBE_IMPORT_FAILED_MESSAGE, download_youtube_audio


def _configure_download(mock_ydl_class: MagicMock, filepath: Path) -> None:
    """Configure yt-dlp to report one completed download at ``filepath``."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {
        "id": "abc123DEF45",
        "title": "Authority regression",
        "duration": 60,
    }
    mock_ydl.prepare_filename.return_value = str(filepath)


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_rejects_foreign_completed_path(
    mock_ydl_class: MagicMock,
    tmp_path: Path,
) -> None:
    """A completed path outside this import directory must never become success metadata."""
    out_dir = tmp_path / "import-cache"
    out_dir.mkdir()
    foreign = tmp_path / "foreign.m4a"
    foreign.write_bytes(b"not-owned-by-this-import")
    _configure_download(mock_ydl_class, foreign)

    result = download_youtube_audio(
        "https://youtube.com/watch?v=abc123DEF45",
        str(out_dir),
    )

    assert result == {
        "ok": False,
        "error": {"code": "download_error", "message": YOUTUBE_IMPORT_FAILED_MESSAGE},
    }
    assert foreign.read_bytes() == b"not-owned-by-this-import"


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_oversize_foreign_completed_path_is_not_deleted(
    mock_ydl_class: MagicMock,
    tmp_path: Path,
) -> None:
    """Oversize rejection must not delete a path outside this import's authority."""
    out_dir = tmp_path / "import-cache"
    out_dir.mkdir()
    foreign = tmp_path / "foreign-oversize.m4a"
    with foreign.open("wb") as handle:
        handle.truncate(DEFAULT_MAX_ENCODED_FILE_BYTES + 1)
    _configure_download(mock_ydl_class, foreign)

    result = download_youtube_audio(
        "https://youtube.com/watch?v=abc123DEF45",
        str(out_dir),
    )

    assert result == {
        "ok": False,
        "error": {"code": "download_error", "message": YOUTUBE_IMPORT_FAILED_MESSAGE},
    }
    assert foreign.exists()
    assert foreign.stat().st_size == DEFAULT_MAX_ENCODED_FILE_BYTES + 1
