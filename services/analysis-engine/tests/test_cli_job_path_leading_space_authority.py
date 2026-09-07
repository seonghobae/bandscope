"""Win32 leading-space normalization regressions for CLI job-file authority."""

from __future__ import annotations

import pytest

from bandscope_analysis import cli  # type: ignore[attr-defined, unused-ignore, import-untyped]


@pytest.mark.parametrize(
    "path",
    [
        r"C:\jobs\ NUL",
        r"C:\jobs\ NUL.txt",
        r"C:\jobs\ COM1 .log",
        r"C:\jobs\ AUX:",
    ],
)
def test_leading_space_reserved_alias_is_rejected_before_filesystem_lookup(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32-normalized device aliases must not acquire filesystem authority."""

    def forbidden_lstat(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("reserved Win32 alias reached filesystem metadata lookup")

    monkeypatch.setattr(cli.os, "lstat", forbidden_lstat)

    with pytest.raises(OSError, match="local regular-file namespace"):
        cli._read_bounded_job_file(path)
