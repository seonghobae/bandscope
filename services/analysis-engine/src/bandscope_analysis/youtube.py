"""YouTube import capabilities for BandScope.

This module provides a safe wrapper around yt-dlp to download audio from YouTube.

Security Notes:
    - URL intake remains host/path/query allowlisted before any network work.
    - Encoded-byte admission uses the same canonical 100 MiB policy as local
      audio. yt-dlp ``max_filesize`` and a progress hook abort in-flight
      transfers so a multi-gigabyte download cannot fill the cache root before
      the post-download check runs.
    - Announced duration must be a finite positive non-Boolean number when
      present; malformed known-duration metadata fails closed before download.
      Download-result duration is revalidated before success so changed
      metadata cannot bypass the same 15-minute admission boundary.
    - Announced ``filesize`` / ``filesize_approx`` values over the policy
      ceiling reject the import before ``download=True``.
    - The completed download path must resolve beneath this import's ``out_dir``
      before post-download size checks, cleanup, or success metadata can use it.
    - The opened-file size is revalidated with ``AudioResourcePolicy`` after
      download; oversize artifacts are deleted.
    - In-flight abort deletes owned ``tmpfilename`` / ``filename`` siblings
      (``.part``, ``.ytdl``, ``-Frag*``) that stay inside this import's
      ``out_dir``. Paths that escape the directory are ignored.
    - Validation errors are payload-free and never include source paths, URLs,
      cookies, or audio content.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
from typing import Any, Dict, Optional

import yt_dlp  # type: ignore

from bandscope_analysis.audio_resource_policy import (
    DEFAULT_AUDIO_RESOURCE_POLICY,
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_MAX_ENCODED_FILE_BYTES,
)

YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_YOUTUBE_URL_LENGTH = 2000
SUPPORTED_AUDIO_EXTENSIONS = (".opus", ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg")
YOUTUBE_DOWNLOAD_FAILED_MESSAGE = (
    "Failed to download audio from YouTube. Please use a local audio file instead."
)
YOUTUBE_IMPORT_FAILED_MESSAGE = "YouTube import failed. Please use a local audio file instead."
YOUTUBE_SIZE_EXCEEDED_MESSAGE = "Selected audio file exceeds the 100 MiB analysis limit."


class YoutubeResourceLimitError(Exception):
    """Fail-closed YouTube admission error that never includes payload paths."""

    def __init__(self, code: str, message: str) -> None:
        """Store a payload-safe public error code and next-action message.

        Args:
            code: Stable machine-readable error code.
            message: User-facing instruction that omits paths and URLs.
        """
        super().__init__(message)
        self.code = code
        self.message = message


def validate_url(url: str) -> bool:
    """
    Validate that a URL is a standard YouTube or youtu.be URL.

    Args:
        url: The URL to validate.

    Returns:
        True if the URL is valid, False otherwise.
    """
    # Pragmatic upper bound to avoid spending parser/downloader work on oversized user input.
    if len(url) > MAX_YOUTUBE_URL_LENGTH:
        return False

    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.netloc.lower().split(":")[0]

        if host == "youtu.be":
            path = parsed.path.strip("/")
            return bool(YOUTUBE_VIDEO_ID_PATTERN.match(path))

        if host in {"youtube.com", "www.youtube.com"}:
            if parsed.path != "/watch":
                return False
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            video_ids = query.get("v", [])
            return len(video_ids) == 1 and bool(YOUTUBE_VIDEO_ID_PATTERN.match(video_ids[0]))

        return False
    except ValueError:
        return False


def _find_downloaded_file(actual_filepath: str) -> Optional[str]:
    """Find the downloaded file, including postprocessor extension changes."""
    if not os.path.exists(actual_filepath):
        # Try to find the file with a different extension in case of conversion
        base_path = os.path.splitext(actual_filepath)[0]
        for ext in SUPPORTED_AUDIO_EXTENSIONS:
            match = base_path + ext
            if os.path.exists(match):
                return match
        return None
    return actual_filepath


def _size_exceeded_result() -> Dict[str, Any]:
    """Return the payload-safe oversize result shared by every admission path."""
    return {
        "ok": False,
        "error": {
            "code": "size_exceeded",
            "message": YOUTUBE_SIZE_EXCEEDED_MESSAGE,
        },
    }


def _download_error_result() -> Dict[str, Any]:
    """Return the payload-safe generic import failure result."""
    return {
        "ok": False,
        "error": {"code": "download_error", "message": YOUTUBE_IMPORT_FAILED_MESSAGE},
    }


def _reject_invalid_or_oversize_duration(info: dict[str, Any]) -> Dict[str, Any] | None:
    """Validate announced duration before authorizing download work.

    Args:
        info: Metadata dictionary from yt-dlp extraction.

    Returns:
        A payload-safe failure for malformed/over-budget known duration, or
        ``None`` when duration is absent or valid and within policy.
    """
    duration = info.get("duration")
    if duration is None:
        return None
    if type(duration) not in (int, float):
        return _download_error_result()
    duration_seconds = float(duration)
    if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
        return _download_error_result()
    if duration_seconds > DEFAULT_MAX_DURATION_SECONDS:
        return {
            "ok": False,
            "error": {
                "code": "duration_exceeded",
                "message": "Video exceeds the 15-minute limit.",
            },
        }
    return None


def _announced_size_exceeds_policy(announced: object) -> bool:
    """Return whether yt-dlp metadata already reports an over-budget file.

    Args:
        announced: Candidate ``filesize`` or ``filesize_approx`` value.

    Returns:
        True when the value is a finite number strictly above the policy ceiling.
    """
    if isinstance(announced, bool) or not isinstance(announced, int | float):
        return False
    if isinstance(announced, float) and not math.isfinite(announced):
        return False
    size_bytes: int | float = announced
    return bool(size_bytes > DEFAULT_MAX_ENCODED_FILE_BYTES)


def _reject_announced_oversize(info: dict[str, Any]) -> Dict[str, Any] | None:
    """Reject before download when extract_info already announced oversize bytes.

    Args:
        info: Metadata dictionary from ``extract_info(..., download=False)``.

    Returns:
        The size-exceeded result, or ``None`` when download may proceed.
    """
    if _announced_size_exceeds_policy(info.get("filesize")) or _announced_size_exceeds_policy(
        info.get("filesize_approx")
    ):
        return _size_exceeded_result()
    return None


def _owned_file_path(path: object, out_dir: str) -> str | None:
    """Return a real path only when it stays inside this import's output directory.

    Args:
        path: Candidate filesystem path from yt-dlp status or sibling lookup.
        out_dir: Directory passed to this import call.

    Returns:
        The resolved file path, or ``None`` when the value is unsafe or foreign.
    """
    if not isinstance(path, str) or path == "":
        return None
    try:
        resolved = os.path.realpath(path)
        root = os.path.realpath(out_dir)
    except OSError:
        return None
    if resolved == root or not resolved.startswith(root + os.sep):
        return None
    return resolved


def _remove_owned_file(path: object, out_dir: str) -> None:
    """Delete one owned regular file, ignoring missing-path races.

    Args:
        path: Candidate path that must resolve inside ``out_dir``.
        out_dir: Directory passed to this import call.
    """
    owned = _owned_file_path(path, out_dir)
    if owned is None:
        return
    try:
        if os.path.isfile(owned):
            os.remove(owned)
    except OSError:
        return


def _remove_download_artifacts(status: dict[str, Any], out_dir: str) -> None:
    """Delete the current download's partial, fragment, and control files.

    Args:
        status: yt-dlp progress-hook payload that may name ``tmpfilename``
            and ``filename``.
        out_dir: Directory passed to this import call.
    """
    stems: set[str] = set()
    for key in ("tmpfilename", "filename"):
        owned = _owned_file_path(status.get(key), out_dir)
        if owned is None:
            continue
        _remove_owned_file(owned, out_dir)
        name = os.path.basename(owned)
        if name.endswith(".part"):
            name = name[: -len(".part")]
        stems.add(name)
    if not stems:
        return
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return
    for entry in entries:
        matches_stem = any(
            entry == stem or entry.startswith(f"{stem}.") or entry.startswith(f"{stem}-")
            for stem in stems
        )
        if matches_stem:
            _remove_owned_file(os.path.join(out_dir, entry), out_dir)


def _abort_over_budget_download(status: dict[str, Any], out_dir: str) -> None:
    """Abort an in-flight download once encoded bytes exceed the policy ceiling.

    Args:
        status: yt-dlp progress-hook payload. Unknown statuses are ignored.
        out_dir: Directory passed to this import call, used to delete partials.
    """
    if status.get("status") not in {"downloading", "finished"}:
        return
    for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
        candidate = status.get(key)
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            continue
        if candidate > DEFAULT_MAX_ENCODED_FILE_BYTES:
            _remove_download_artifacts(status, out_dir)
            raise YoutubeResourceLimitError("size_exceeded", YOUTUBE_SIZE_EXCEEDED_MESSAGE)


def _make_abort_hook(out_dir: str) -> Any:
    """Bind the in-flight abort hook to one import output directory.

    Args:
        out_dir: Directory passed to this import call.

    Returns:
        A yt-dlp progress hook that aborts and deletes owned partials.
    """

    def _bound_abort_over_budget_download(status: dict[str, Any]) -> None:
        """Abort and delete owned partials for this import directory.

        Args:
            status: yt-dlp progress-hook payload.
        """
        _abort_over_budget_download(status, out_dir)

    return _bound_abort_over_budget_download


def _handle_download_error(e: yt_dlp.utils.DownloadError) -> Dict[str, Any]:
    """Map yt-dlp DownloadError to the public YouTube import error response."""
    msg = str(e).lower()
    if "max-filesize" in msg or "100 mib" in msg:
        return _size_exceeded_result()
    if (
        "sign in" in msg
        or "members-only" in msg
        or "private" in msg
        or "geo" in msg
        or "premium" in msg
    ):
        return {
            "ok": False,
            "error": {
                "code": "restricted_content",
                "message": (
                    "This video is restricted (login, paywall, or geo-blocked). "
                    "Please use a local audio file instead."
                ),
            },
        }
    return {
        "ok": False,
        "error": {
            "code": "download_failed",
            "message": YOUTUBE_DOWNLOAD_FAILED_MESSAGE,
        },
    }


def download_youtube_audio(url: str, out_dir: str) -> Dict[str, Any]:
    """
    Download audio from a YouTube URL to the specified directory.

    Args:
        url: The YouTube URL to download.
        out_dir: The directory to save the audio file.

    Returns:
        A dictionary containing the result of the download.
    """
    if not validate_url(url):
        return {
            "ok": False,
            "error": {
                "code": "unsupported_url",
                "message": "Only standard YouTube URLs are supported.",
            },
        }

    ydl_opts: Dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "postprocessors": [{"key": "FFmpegExtractAudio"}],
        "geo_bypass": False,
        "max_filesize": DEFAULT_MAX_ENCODED_FILE_BYTES,
        "progress_hooks": [_make_abort_hook(out_dir)],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise Exception("Failed to extract info")
            duration_rejection = _reject_invalid_or_oversize_duration(info)
            if duration_rejection is not None:
                return duration_rejection
            announced_rejection = _reject_announced_oversize(info)
            if announced_rejection is not None:
                return announced_rejection

            info = ydl.extract_info(url, download=True)
            if info is None:
                raise Exception("Failed to extract info")
            actual_filepath = ydl.prepare_filename(info)
            actual_filepath = _find_downloaded_file(actual_filepath)

            if actual_filepath is None:
                return {
                    "ok": False,
                    "error": {
                        "code": "file_not_found",
                        "message": "Downloaded file could not be found.",
                    },
                }

            owned_filepath = _owned_file_path(actual_filepath, out_dir)
            if owned_filepath is None:
                return _download_error_result()
            actual_filepath = owned_filepath

            duration_rejection = _reject_invalid_or_oversize_duration(info)
            if duration_rejection is not None:
                _remove_owned_file(actual_filepath, out_dir)
                return duration_rejection

            try:
                DEFAULT_AUDIO_RESOURCE_POLICY.validate_encoded_file_bytes(
                    os.path.getsize(actual_filepath)
                )
            except ValueError:
                if os.path.exists(actual_filepath):
                    os.remove(actual_filepath)
                return _size_exceeded_result()
            return {
                "ok": True,
                "metadata": {
                    "id": info.get("id"),
                    "title": info.get("title"),
                    "duration": info.get("duration"),
                    "filepath": actual_filepath,
                },
            }
    except YoutubeResourceLimitError:
        return _size_exceeded_result()
    except yt_dlp.utils.DownloadError as e:
        return _handle_download_error(e)
    except Exception:
        return _download_error_result()


def main() -> None:
    """Run as a script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    result = download_youtube_audio(args.url, args.out_dir)
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
