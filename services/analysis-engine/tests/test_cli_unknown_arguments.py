"""Regression tests for fail-closed explicit CLI argument dispatch."""

from __future__ import annotations

import io
import json

import pytest

from bandscope_analysis import cli


class _ForbiddenRead:
    """Reject standard-input reads for malformed explicit argument modes."""

    def read(self, size: int = -1) -> bytes:
        """Raise whenever argument dispatch falls through to standard input."""
        raise AssertionError("malformed explicit CLI arguments must not read stdin")


class _BinaryStdin:
    """Expose a process-like binary stdin buffer that must remain unread."""

    def __init__(self) -> None:
        """Attach the forbidden read sentinel."""
        self.buffer = _ForbiddenRead()


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["cli.py", "--unknown"], "Unsupported CLI arguments"),
        (
            ["cli.py", "--status", "unexpected-extra-argument"],
            "--status does not accept additional arguments",
        ),
    ],
)
def test_cli_rejects_other_malformed_explicit_arguments_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    message: str,
) -> None:
    """Every malformed explicit argument form must fail before touching stdin."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", argv)
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin())
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == message
