"""Tests for YouTube import capabilities."""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp  # type: ignore

from bandscope_analysis.audio_resource_policy import DEFAULT_MAX_ENCODED_FILE_BYTES
from bandscope_analysis.youtube import (
    MAX_YOUTUBE_URL_LENGTH,
    YOUTUBE_SIZE_EXCEEDED_MESSAGE,
    _owned_file_path,
    _remove_download_artifacts,
    _remove_owned_file,
    download_youtube_audio,
    validate_url,
)


def test_validate_url() -> None:
    """Test URL validation."""
    assert validate_url("https://youtube.com/watch?v=abc123DEF45") is True
    assert validate_url("https://youtu.be/abc123DEF45") is True
    assert validate_url("https://www.youtube.com/watch?v=abc123DEF45") is True
    assert validate_url("https://www.youtube.com/watch?v=abc123DEF45&t=10") is True
    url_prefix = "https://youtube.com/watch?v=abc123DEF45&x="
    max_length_url = url_prefix + ("a" * (MAX_YOUTUBE_URL_LENGTH - len(url_prefix)))
    long_query_url = max_length_url + "a"

    assert validate_url(max_length_url) is True
    assert validate_url("https://m.youtube.com/watch?v=abc123DEF45") is False
    assert validate_url("https://music.youtube.com/watch?v=abc123DEF45") is False
    assert validate_url("https://evil.youtube.com/watch?v=abc123DEF45") is False
    assert validate_url("https://youtube.com/watch?v=123") is False
    assert validate_url("https://youtu.be/123") is False
    assert validate_url("http://youtube.com/watch?v=abc123DEF45") is False
    assert validate_url("https://vimeo.com/abc123DEF45") is False
    assert validate_url("https://youtube.com/redirect?q=https://example.com") is False
    assert validate_url("https://www.youtube.com/redirect?q=https://example.com") is False
    assert validate_url("https://youtube.com/watch?v=") is False
    assert validate_url("https://youtu.be/") is False
    assert validate_url("https://youtu.be/abc123DEF45/extra") is False
    assert validate_url("https://youtube.com/watch?v=abc123DEF45&v=def456GHI78") is False
    assert validate_url("https://youtube.com/watch?v=&v=def456GHI78") is False
    assert validate_url("https://youtube.com/watch?v=abc123DEF45&v=") is False
    assert validate_url("https://youtube.com/watch?v=../../../etc/passwd") is False
    assert validate_url("https://youtu.be/../../../etc/passwd") is False
    assert validate_url(long_query_url) is False


def test_validate_url_edge_cases() -> None:
    """Test URL validation edge cases and potential bypasses."""
    # IP address bypass attempts
    assert validate_url("https://127.0.0.1/watch?v=123") is False
    assert validate_url("https://[::1]/watch?v=123") is False

    # User info bypass attempts
    assert validate_url("https://youtube.com@evil.com/watch?v=123") is False
    assert validate_url("https://youtube.com@youtu.be/123") is False
    assert validate_url("https://user:pass@youtube.com/watch?v=123") is False

    # Subdomain/Suffix trickery
    assert validate_url("https://youtube.com.evil.com/watch?v=123") is False
    assert validate_url("https://evil-youtube.com/watch?v=123") is False

    # Path/Query trickery
    assert validate_url("https://evil.com/youtube.com/watch?v=123") is False
    assert validate_url("https://evil.com?youtube.com/watch?v=123") is False
    assert validate_url("https://evil.com#youtube.com/watch?v=123") is False

    # Allowlist behavior and explicit default ports
    assert validate_url("https://kr.youtube.com/watch?v=abc123DEF45") is False
    assert validate_url("https://youtube.com:443/watch?v=abc123DEF45") is True


def test_download_youtube_audio_invalid_url() -> None:
    """Test downloading with an invalid URL."""
    result = download_youtube_audio("https://vimeo.com/abc123DEF45", "/tmp")
    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_url"


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_success(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """Test successful download."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    mock_info = {
        "id": "abc123DEF45",
        "title": "Test Video",
        "duration": 60,
        "filesize": True,
        "filesize_approx": float("nan"),
    }
    out_dir = str(Path("/tmp").resolve())
    mock_ydl.extract_info.return_value = mock_info
    mock_ydl.prepare_filename.return_value = f"{out_dir}/abc123DEF45.webm"
    mock_exists.return_value = True
    mock_getsize.return_value = 10 * 1024 * 1024

    input_url = "https://youtube.com/watch?v=abc123DEF45"
    result = download_youtube_audio(input_url, out_dir)

    assert result["ok"] is True
    assert result["metadata"]["id"] == "abc123DEF45"
    assert result["metadata"]["title"] == "Test Video"
    assert result["metadata"]["duration"] == 60
    assert result["metadata"]["filepath"] == f"{out_dir}/abc123DEF45.webm"

    # Assert that YoutubeDL was initialized with the correct options
    mock_ydl_class.assert_called_once()
    called_opts = mock_ydl_class.call_args[0][0]
    assert called_opts["format"] == "bestaudio/best"
    assert called_opts["quiet"] is True
    assert called_opts["no_warnings"] is True
    assert called_opts["noprogress"] is True
    assert called_opts["noplaylist"] is True
    assert called_opts["geo_bypass"] is False
    assert called_opts["postprocessors"] == [{"key": "FFmpegExtractAudio"}]
    assert called_opts["max_filesize"] == DEFAULT_MAX_ENCODED_FILE_BYTES
    assert called_opts["progress_hooks"]
    assert "%(id)s.%(ext)s" in called_opts["outtmpl"]

    # Verify extract_info was called twice correctly: once for metadata, once for download
    from unittest.mock import call

    assert mock_ydl.extract_info.call_count == 2
    mock_ydl.extract_info.assert_has_calls(
        [
            call(input_url, download=False),
            call(input_url, download=True),
        ]
    )


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_converted_extension(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """Test successful download when the file is converted to another extension."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    mock_info = {
        "id": "abc123DEF45",
        "title": "Test Video",
        "duration": 60,
    }
    out_dir = str(Path("/tmp").resolve())
    mock_ydl.extract_info.return_value = mock_info
    mock_ydl.prepare_filename.return_value = f"{out_dir}/abc123DEF45.webm"

    # os.path.exists returns False for .webm, but True for the converted .opus.
    def exists_side_effect(path: str) -> bool:
        """Mock exists function to simulate converted extension file presence."""
        return path == f"{out_dir}/abc123DEF45.opus"

    mock_exists.side_effect = exists_side_effect
    mock_getsize.return_value = 10 * 1024 * 1024

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", out_dir)

    assert result["ok"] is True
    assert result["metadata"]["filepath"] == f"{out_dir}/abc123DEF45.opus"


@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_file_not_found(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
) -> None:
    """Test failure when the downloaded file cannot be found."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    mock_info = {
        "id": "abc123DEF45",
        "title": "Test Video",
        "duration": 60,
    }
    mock_ydl.extract_info.return_value = mock_info
    mock_ydl.prepare_filename.return_value = "/tmp/abc123DEF45.webm"
    mock_exists.return_value = False

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "file_not_found"


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_info_none(mock_ydl_class: MagicMock) -> None:
    """Test when extract_info returns None."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    mock_ydl.extract_info.return_value = None

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "download_error"
    assert result["error"]["message"] == (
        "YouTube import failed. Please use a local audio file instead."
    )


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_restricted(mock_ydl_class: MagicMock) -> None:
    """Test when download fails due to restrictions."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("Sign in to confirm")

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "restricted_content"
    assert "restricted" in result["error"]["message"]


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_generic_download_error(mock_ydl_class: MagicMock) -> None:
    """Test when download fails with a generic error."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    raw_error = (
        "Some random network error for https://youtube.com/watch?v=abc123DEF45"
        " with cookie=secret and /Users/test/local/path"
    )
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(raw_error)

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "download_failed"
    assert result["error"]["message"] == (
        "Failed to download audio from YouTube. Please use a local audio file instead."
    )
    assert raw_error not in result["error"]["message"]
    assert "cookie=secret" not in result["error"]["message"]
    assert "/Users/test/local/path" not in result["error"]["message"]


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_exception(mock_ydl_class: MagicMock) -> None:
    """Test when an unexpected exception occurs."""
    raw_error = "Unexpected explosion with token=secret in /Users/test/private/path"
    mock_ydl_class.side_effect = ValueError(raw_error)

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "download_error"
    assert result["error"]["message"] == (
        "YouTube import failed. Please use a local audio file instead."
    )
    assert raw_error not in result["error"]["message"]
    assert "token=secret" not in result["error"]["message"]
    assert "/Users/test/private/path" not in result["error"]["message"]


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_duration_exceeded(mock_ydl_class: MagicMock) -> None:
    """Test download fails if duration exceeds 15 minutes."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 16 * 60}

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")
    assert result["ok"] is False
    assert result["error"]["code"] == "duration_exceeded"


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_accepts_size_between_legacy_and_canonical_ceiling(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """A 60 MiB download that the old 50 MB check rejected is now accepted."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    out_dir = str(Path("/tmp").resolve())
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 10 * 60}
    mock_ydl.prepare_filename.return_value = f"{out_dir}/abc123DEF45.m4a"
    mock_exists.return_value = True
    mock_getsize.return_value = 60 * 1024 * 1024

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", out_dir)

    assert result["ok"] is True
    assert result["metadata"]["filepath"] == f"{out_dir}/abc123DEF45.m4a"


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_accepts_exact_policy_ceiling(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """An encoded YouTube file exactly at the 100 MiB ceiling is accepted."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 10 * 60}
    mock_ydl.prepare_filename.return_value = "/tmp/abc123DEF45.m4a"
    mock_exists.return_value = True
    mock_getsize.return_value = DEFAULT_MAX_ENCODED_FILE_BYTES

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is True


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.os.remove")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_size_exceeded(
    mock_ydl_class: MagicMock,
    mock_remove: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """Post-download files one byte over the canonical 100 MiB ceiling are deleted."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    out_dir = str(Path("/tmp").resolve())
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 10 * 60}
    mock_ydl.prepare_filename.return_value = f"{out_dir}/abc123DEF45.m4a"
    mock_exists.return_value = True
    mock_getsize.return_value = DEFAULT_MAX_ENCODED_FILE_BYTES + 1

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", out_dir)
    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE
    mock_remove.assert_called_with(f"{out_dir}/abc123DEF45.m4a")


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.os.remove")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_oversize_skips_remove_when_file_already_gone(
    mock_ydl_class: MagicMock,
    mock_remove: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """A vanished oversize artifact still fails closed without a remove race."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 10 * 60}
    mock_ydl.prepare_filename.return_value = "/tmp/abc123DEF45.m4a"
    mock_exists.side_effect = [True, False]
    mock_getsize.return_value = DEFAULT_MAX_ENCODED_FILE_BYTES + 1

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    mock_remove.assert_not_called()


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_rejects_announced_filesize_before_download(
    mock_ydl_class: MagicMock,
) -> None:
    """Announced filesize over the policy ceiling must not start the download."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {
        "id": "abc123DEF45",
        "duration": 60,
        "filesize": DEFAULT_MAX_ENCODED_FILE_BYTES + 1,
    }

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE
    mock_ydl.extract_info.assert_called_once_with(
        "https://youtube.com/watch?v=abc123DEF45",
        download=False,
    )


@pytest.mark.parametrize(
    "info",
    [
        {
            "id": "abc123DEF45",
            "duration": 60,
            "filesize_approx": DEFAULT_MAX_ENCODED_FILE_BYTES + 1,
        },
        {
            "id": "abc123DEF45",
            "duration": 60,
            "filesize_approx": float(DEFAULT_MAX_ENCODED_FILE_BYTES) + 0.5,
        },
    ],
)
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_rejects_announced_approximate_oversize(
    mock_ydl_class: MagicMock,
    info: dict[str, object],
) -> None:
    """Approximate oversize metadata rejects the import before download starts."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = info

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    mock_ydl.extract_info.assert_called_once()


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_progress_hook_aborts_over_budget(
    mock_ydl_class: MagicMock,
) -> None:
    """In-flight progress that crosses the encoded-byte ceiling fails closed."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    def extract_info(_url: str, download: bool = False) -> dict[str, object]:
        """Invoke the registered progress hook when the download starts."""
        if download:
            hook = mock_ydl_class.call_args[0][0]["progress_hooks"][0]
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": DEFAULT_MAX_ENCODED_FILE_BYTES + 1,
                }
            )
        return {"id": "abc123DEF45", "duration": 60}

    mock_ydl.extract_info.side_effect = extract_info

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_progress_hook_deletes_partial_artifacts(
    mock_ydl_class: MagicMock,
    tmp_path: Path,
) -> None:
    """In-flight abort must delete written partials so they cannot fill the cache."""
    out_dir = tmp_path / "import-cache"
    out_dir.mkdir()
    outsider = tmp_path / "unrelated-youtube-partial.part"
    partial = out_dir / "abc123DEF45.m4a.part"
    fragment = out_dir / "abc123DEF45.m4a-Frag1"
    control = out_dir / "abc123DEF45.m4a.ytdl"
    keep = out_dir / "keep-me.txt"
    partial.write_bytes(b"partial-cache-bytes")
    fragment.write_bytes(b"hls-fragment-bytes")
    control.write_bytes(b"ytdl-control-bytes")
    keep.write_bytes(b"unrelated-cache-note")
    outsider.write_bytes(b"must-not-delete")
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    def extract_info(_url: str, download: bool = False) -> dict[str, object]:
        """Abort after yt-dlp has already written the current block to disk."""
        if download:
            hook = mock_ydl_class.call_args[0][0]["progress_hooks"][0]
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": DEFAULT_MAX_ENCODED_FILE_BYTES + 1,
                    "tmpfilename": str(partial),
                    "filename": str(out_dir / "abc123DEF45.m4a"),
                }
            )
        return {"id": "abc123DEF45", "duration": 60}

    mock_ydl.extract_info.side_effect = extract_info

    result = download_youtube_audio(
        "https://youtube.com/watch?v=abc123DEF45",
        str(out_dir),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE
    assert not partial.exists()
    assert not fragment.exists()
    assert not control.exists()
    assert keep.exists()
    assert outsider.exists()


def test_owned_file_path_rejects_empty_foreign_and_unresolvable_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abort cleanup must not follow empty, escaped, or unresolvable paths."""
    out_dir = tmp_path / "import-cache"
    out_dir.mkdir()
    escaped = tmp_path / "outside.part"
    escaped.write_bytes(b"keep")

    assert _owned_file_path(None, str(out_dir)) is None
    assert _owned_file_path("", str(out_dir)) is None
    assert _owned_file_path(str(out_dir), str(out_dir)) is None
    assert _owned_file_path(str(escaped), str(out_dir)) is None

    def boom(_path: str) -> str:
        """Simulate a filesystem error while resolving a candidate path."""
        raise OSError("realpath failed")

    monkeypatch.setattr("bandscope_analysis.youtube.os.path.realpath", boom)
    assert _owned_file_path(str(out_dir / "clip.part"), str(out_dir)) is None


def test_remove_owned_file_ignores_missing_directories_and_remove_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owned cleanup skips non-files and treats remove races as already gone."""
    out_dir = tmp_path / "import-cache"
    nested = out_dir / "nested-dir"
    nested.mkdir(parents=True)
    _remove_owned_file(None, str(out_dir))
    _remove_owned_file(str(nested), str(out_dir))
    assert nested.is_dir()

    target = out_dir / "clip.part"
    target.write_bytes(b"partial")

    def boom(_path: str) -> None:
        """Simulate a disappearing file during abort cleanup."""
        raise OSError("remove failed")

    monkeypatch.setattr("bandscope_analysis.youtube.os.remove", boom)
    _remove_owned_file(str(target), str(out_dir))
    assert target.exists()


def test_remove_download_artifacts_skips_empty_status_and_unlistable_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact sweep no-ops when yt-dlp omitted paths or the cache vanished."""
    out_dir = tmp_path / "import-cache"
    out_dir.mkdir()
    leftover = out_dir / "other-file.txt"
    leftover.write_bytes(b"keep")
    _remove_download_artifacts({"tmpfilename": None, "filename": 12}, str(out_dir))
    assert leftover.exists()

    partial = out_dir / "abc123DEF45.m4a.part"
    partial.write_bytes(b"partial")

    def boom(_path: str) -> list[str]:
        """Simulate the import cache disappearing after the first delete."""
        raise OSError("listdir failed")

    monkeypatch.setattr("bandscope_analysis.youtube.os.listdir", boom)
    _remove_download_artifacts({"tmpfilename": str(partial)}, str(out_dir))
    assert leftover.exists()


@patch("bandscope_analysis.youtube.os.path.getsize")
@patch("bandscope_analysis.youtube.os.path.exists")
@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_progress_hook_ignores_non_budget_updates(
    mock_ydl_class: MagicMock,
    mock_exists: MagicMock,
    mock_getsize: MagicMock,
) -> None:
    """Unknown statuses and non-integer byte fields do not abort a valid download."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45", "duration": 60}
    mock_ydl.prepare_filename.return_value = "/tmp/abc123DEF45.m4a"
    mock_exists.return_value = True
    mock_getsize.return_value = 10 * 1024 * 1024

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")
    hook = mock_ydl_class.call_args[0][0]["progress_hooks"][0]
    hook({"status": "error"})
    hook({"status": "downloading", "downloaded_bytes": True})
    hook({"status": "downloading", "downloaded_bytes": 12.5})
    hook({"status": "downloading", "downloaded_bytes": 10})
    hook({"status": "finished", "total_bytes": DEFAULT_MAX_ENCODED_FILE_BYTES})

    assert result["ok"] is True


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_maps_max_filesize_download_error(
    mock_ydl_class: MagicMock,
) -> None:
    """yt-dlp max-filesize aborts become the payload-safe size-exceeded result."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
        "File is larger than max-filesize"
    )

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE
    assert "max-filesize" not in result["error"]["message"]


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_maps_mib_limit_download_error(
    mock_ydl_class: MagicMock,
) -> None:
    """Download errors that mention the 100 MiB ceiling stay payload-safe."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(YOUTUBE_SIZE_EXCEEDED_MESSAGE)

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "size_exceeded"
    assert result["error"]["message"] == YOUTUBE_SIZE_EXCEEDED_MESSAGE


def test_main_block(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Test the CLI entry point."""
    test_args = [
        "youtube.py",
        "--url",
        "https://youtube.com/watch?v=abc123DEF45",
        "--out-dir",
        "/tmp",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    import bandscope_analysis.youtube

    importlib.reload(bandscope_analysis.youtube)

    with patch("bandscope_analysis.youtube.download_youtube_audio") as mock_download:
        mock_download.return_value = {"ok": True, "metadata": {"id": "abc123DEF45"}}

        with patch.object(sys, "exit") as mock_exit:
            bandscope_analysis.youtube.main()
            mock_exit.assert_called_with(0)

            # test failure exit 1
            mock_download.return_value = {"ok": False}
            bandscope_analysis.youtube.main()
            mock_exit.assert_called_with(1)


def test_module_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Test module execution against a real owned output path without network I/O."""
    import runpy

    import bandscope_analysis.youtube

    downloaded_path = tmp_path / "abc123DEF45.m4a"
    downloaded_path.write_bytes(b"test-audio")
    test_args = [
        "youtube.py",
        "--url",
        "https://youtube.com/watch?v=abc123DEF45",
        "--out-dir",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    # Mock only the downloader/network boundary. Real filesystem semantics are
    # required so the completed-path ownership check remains exercised.
    mock_yt_dlp = MagicMock()
    mock_ydl = MagicMock()
    mock_yt_dlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"id": "abc123DEF45"}
    mock_ydl.prepare_filename.return_value = str(downloaded_path)
    monkeypatch.setitem(sys.modules, "yt_dlp", mock_yt_dlp)

    with patch.object(sys, "exit") as mock_exit:
        runpy.run_path(bandscope_analysis.youtube.__file__, run_name="__main__")
        mock_exit.assert_called_with(0)


@patch("bandscope_analysis.youtube.urllib.parse.urlparse")
def test_validate_url_exception(mock_urlparse: MagicMock) -> None:
    """Test URL validation exception handling."""
    mock_urlparse.side_effect = ValueError("Test exception")
    assert validate_url("https://youtube.com/watch?v=abc123DEF45") is False


@patch("bandscope_analysis.youtube.yt_dlp.YoutubeDL")
def test_download_youtube_audio_second_info_none(mock_ydl_class: MagicMock) -> None:
    """Test when the second extract_info returns None."""
    mock_ydl = MagicMock()
    mock_ydl_class.return_value.__enter__.return_value = mock_ydl

    # First call (download=False) returns info, second call (download=True) returns None
    mock_ydl.extract_info.side_effect = [{"duration": 60}, None]

    result = download_youtube_audio("https://youtube.com/watch?v=abc123DEF45", "/tmp")

    assert result["ok"] is False
    assert result["error"]["code"] == "download_error"
    assert result["error"]["message"] == (
        "YouTube import failed. Please use a local audio file instead."
    )
