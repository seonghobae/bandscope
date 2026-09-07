"""Regression tests for CLI job-file local-path authority."""

from __future__ import annotations

import pytest

from bandscope_analysis import cli  # type: ignore[attr-defined, unused-ignore, import-untyped]


@pytest.mark.parametrize(
    "path",
    [
        r"\\server\share\job.json",
        "//server/share/job.json",
        r"\\?\UNC\server\share\job.json",
        r"\\.\pipe\bandscope-job",
        r"/\server\share\job.json",
        r"\/server\share\job.json",
        r"/\.\pipe\bandscope-job",
        r"/\?\UNC\server\share\job.json",
    ],
)
def test_remote_or_device_job_paths_fail_before_filesystem_lookup(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """UNC/device namespace input must not reach metadata or open system calls."""

    def forbidden_lstat(*_args: object, **_kwargs: object) -> object:
        """Fail if lexical rejection happens after a filesystem lookup."""
        raise AssertionError("unsafe job path reached os.lstat")

    def forbidden_open(*_args: object, **_kwargs: object) -> int:
        """Fail if lexical rejection happens after descriptor acquisition."""
        raise AssertionError("unsafe job path reached os.open")

    monkeypatch.setattr(cli.os, "lstat", forbidden_lstat)
    monkeypatch.setattr(cli.os, "open", forbidden_open)

    with pytest.raises(OSError, match="local regular-file namespace"):
        cli._read_bounded_job_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "C:",
        r"C:job.json",
        r"D:..\job.json",
    ],
)
def test_windows_drive_relative_job_paths_fail_before_filesystem_lookup(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Drive-relative Win32 input must not inherit per-drive current-directory authority."""

    def forbidden_lstat(*_args: object, **_kwargs: object) -> object:
        """Prove lexical rejection happens before any filesystem metadata lookup."""
        raise AssertionError("drive-relative job path reached os.lstat")

    monkeypatch.setattr(cli.os, "lstat", forbidden_lstat)

    with pytest.raises(OSError, match="local regular-file namespace"):
        cli._read_bounded_job_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "job.json:secret",
        "job.json::$DATA",
        r"C:\tmp\job.json:secret",
    ],
)
def test_windows_alternate_stream_job_paths_fail_before_filesystem_lookup(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """NTFS alternate-stream syntax must stay outside the regular-file job namespace."""

    def forbidden_lstat(*_args: object, **_kwargs: object) -> object:
        """Fail if alternate-stream syntax reaches the filesystem boundary."""
        raise AssertionError("alternate stream job path reached os.lstat")

    monkeypatch.setattr(cli.os, "lstat", forbidden_lstat)

    with pytest.raises(OSError):
        cli._read_bounded_job_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "NUL",
        "nul.txt",
        "NUL:",
        "NUL ",
        "NUL .txt",
        "CON",
        "CONIN$",
        "CONOUT$:",
        "CLOCK$",
        "PRN.json",
        "AUX",
        "COM1",
        "COM1 .log",
        "com9.json",
        "LPT1.txt",
        "parent/COM¹.log",
        r"parent\LPT³.json",
    ],
)
def test_windows_device_aliases_fail_before_filesystem_lookup(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Reserved Win32 device aliases must be rejected before path lookup."""

    def forbidden_lstat(*_args: object, **_kwargs: object) -> object:
        """Fail if a reserved device alias reaches the filesystem boundary."""
        raise AssertionError("Windows device alias reached os.lstat")

    monkeypatch.setattr(cli.os, "lstat", forbidden_lstat)

    with pytest.raises(OSError):
        cli._read_bounded_job_file(path)
