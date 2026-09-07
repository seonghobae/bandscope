"""Regression tests for bounded analysis-engine CLI input reads."""

from __future__ import annotations

import io
import json

import pytest

from bandscope_analysis import cli


class _BoundedReadRequired(io.BytesIO):
    """Fail when production attempts an unbounded binary stream read."""

    def read(self, size: int = -1) -> bytes:
        """Read only when the caller supplies the exact bounded-size envelope."""
        if not 0 <= size <= cli.MAX_JSON_FILE_SIZE + 1:
            raise AssertionError("CLI stdin read must use the maximum bounded size")
        return super().read(size)


class _OversizedInput:
    """Provide an oversized byte payload without retaining it in the fixture."""

    def read(self, size: int = -1) -> bytes:
        """Return exactly the requested amount so production observes overflow."""
        if not 0 <= size <= cli.MAX_JSON_FILE_SIZE + 1:
            raise AssertionError("CLI stdin read must use the maximum bounded size")
        return b"x" * size


class _ForbiddenRead:
    """Fail if an explicit CLI argument unexpectedly consumes standard input."""

    def read(self, size: int = -1) -> bytes:
        """Reject every read because explicit argument modes own their input source."""
        raise AssertionError("explicit CLI arguments must not read stdin")


class _BinaryStdin:
    """Expose a binary buffer like the standard process stdin wrapper."""

    def __init__(self, buffer: _BoundedReadRequired | _OversizedInput | _ForbiddenRead) -> None:
        """Attach the bounded binary stream used by the CLI."""
        self.buffer = buffer


def _stdin_bytes(payload: bytes) -> _BinaryStdin:
    """Return process-like stdin backed by explicitly bounded bytes."""
    return _BinaryStdin(_BoundedReadRequired(payload))


def test_cli_stdin_read_uses_explicit_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal stdin job must never trigger an unbounded binary ``read()`` call."""
    payload = json.dumps(
        {
            "jobId": "bounded-stdin",
            "request": {
                "sourceKind": "demo",
                "sourceLabel": "Bounded Input",
                "roleFocus": ["bass-guitar"],
            },
        }
    ).encode("utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", _stdin_bytes(payload))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["jobId"] == "bounded-stdin"


def test_cli_rejects_oversized_stdin_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized stdin is rejected after one bounded byte read, before JSON parsing."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_OversizedInput()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == "Job input exceeds maximum size limit"


def test_cli_stdin_limit_is_measured_in_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multibyte stdin must not bypass the advertised byte-size boundary."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli, "MAX_JSON_FILE_SIZE", 8)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", _stdin_bytes(("é" * 5).encode("utf-8")))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["error"]["message"] == "Job input exceeds maximum size limit"


def test_cli_rejects_invalid_utf8_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid UTF-8 stdin must fail with a stable payload-free error."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", _stdin_bytes(b"\xff"))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == "Job input must be valid UTF-8"


def test_cli_text_only_stdin_rejects_surrogate_before_size_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text-only compatibility stdin must translate surrogate encoding failures safely."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('{"jobId":"' + chr(0xDCFF) + '"}'))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == "Job input must be valid UTF-8"


def test_cli_inline_job_argument_obeys_input_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inline ``--job`` payload cannot bypass the common JSON byte limit."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli, "MAX_JSON_FILE_SIZE", 8)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", '{"jobId":"é"}'])
    monkeypatch.setattr(cli.sys, "stdin", _stdin_bytes(b""))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["error"]["message"] == "Job input exceeds maximum size limit"


def test_cli_inline_job_rejects_surrogate_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surrogate-bearing argv payload must fail with the stable UTF-8 error."""
    surrogate_payload = '{"jobId":"' + chr(0xDCFF) + '"}'
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", surrogate_payload])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == "Job input must be valid UTF-8"


def test_cli_job_file_limit_is_measured_in_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A multibyte job file is rejected by bytes even when character count is small."""
    job_file = tmp_path / "multibyte_job.json"
    job_file.write_text("é" * 5, encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(cli, "MAX_JSON_FILE_SIZE", 8)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", str(job_file)])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["error"]["message"] == "Job file exceeds maximum size limit"


def test_cli_status_argument_does_not_consume_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status command must return immediately without waiting for standard input."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--status"])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["status"] == "ready"


def test_cli_job_argument_does_not_consume_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit inline job must not block on or consume unrelated standard input."""
    payload = json.dumps(
        {
            "jobId": "argument-job",
            "request": {
                "sourceKind": "demo",
                "sourceLabel": "Argument Input",
                "roleFocus": ["bass-guitar"],
            },
        }
    )
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", payload])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["jobId"] == "argument-job"


def test_cli_inline_job_with_leading_json_whitespace_does_not_become_a_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leading JSON whitespace must preserve inline ``--job`` dispatch semantics."""
    payload = " \n\t" + json.dumps(
        {
            "jobId": "whitespace-argument-job",
            "request": {
                "sourceKind": "demo",
                "sourceLabel": "Whitespace Argument Input",
                "roleFocus": ["bass-guitar"],
            },
        }
    )
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", payload])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["jobId"] == "whitespace-argument-job"


def test_cli_non_json_whitespace_prefix_remains_a_job_file_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Unicode whitespace outside JSON grammar must not redefine a file operand."""
    job_file_name = "\u00a0{job.json"
    job_file = tmp_path / job_file_name
    job_file.write_text(
        json.dumps(
            {
                "jobId": "unicode-space-file-job",
                "request": {
                    "sourceKind": "demo",
                    "sourceLabel": "Unicode Space File Input",
                    "roleFocus": ["bass-guitar"],
                },
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", job_file_name])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["jobId"] == "unicode-space-file-job"


@pytest.mark.parametrize(
    "argv",
    [
        ["cli.py", "--job"],
        ["cli.py", "--job", "{}", "unexpected-extra-argument"],
    ],
)
def test_cli_malformed_job_arguments_fail_without_consuming_stdin(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Malformed explicit ``--job`` usage must fail immediately instead of reading stdin."""
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", argv)
    monkeypatch.setattr(cli.sys, "stdin", _BinaryStdin(_ForbiddenRead()))
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 1
    response = json.loads(stdout.getvalue())
    assert response["state"] == "failed"
    assert response["error"]["message"] == "--job requires exactly one JSON payload or file path"
