"""Regression tests for stem-separation logging privacy."""

import logging

import pytest

import bandscope_analysis.api as analysis_api


class _ResultQueue:
    """Capture the worker result without starting a multiprocessing queue."""

    def __init__(self) -> None:
        self.items: list[tuple[object, object]] = []

    def put(self, item: tuple[object, object]) -> None:
        """Record one result emitted by the worker."""
        self.items.append(item)


class _FailingSeparator:
    """Raise dependency-controlled sensitive text from the separator boundary."""

    def separate(self, source_path: str) -> dict[str, object]:
        """Simulate a dependency failure after receiving an authorized source path."""
        raise RuntimeError(
            f"decoder failed for {source_path} /Users/Alice/private-song.wav token=super-secret"
        )


def _local_audio_request() -> dict[str, object]:
    """Return a valid local-audio request without cache or temporary-path authority."""
    return {
        "sourceKind": "local_audio",
        "projectId": "privacy-regression",
        "sourceLabel": "private-song.wav",
        "roleFocus": ["bass-guitar"],
        "localSource": {
            "sourcePath": "/private/customer/Alice/session.wav",
            "fileName": "private-song.wav",
            "extension": "wav",
            "fileSizeBytes": 1024,
        },
    }


def _assert_payload_free_log(caplog: pytest.LogCaptureFixture) -> None:
    """Require routine logs to omit dependency payloads and exception tracebacks."""
    assert "/private/customer/Alice/session.wav" not in caplog.text
    assert "/Users/Alice/private-song.wav" not in caplog.text
    assert "private-song.wav token=super-secret" not in caplog.text
    assert "super-secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_stem_worker_failure_log_omits_dependency_payload_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Routine worker diagnostics must not retain dependency payloads or tracebacks."""
    result_queue = _ResultQueue()
    source_path = "/private/customer/Alice/session.wav"

    monkeypatch.setattr(analysis_api, "AudioStemSeparator", _FailingSeparator)
    caplog.set_level(logging.ERROR, logger=analysis_api.__name__)

    analysis_api._stem_separation_worker(source_path, result_queue)

    assert result_queue.items == [
        ("runtime_error", "Runtime error occurred during stem separation.")
    ]
    assert "Stem separation failed with a runtime error." in caplog.text
    _assert_payload_free_log(caplog)


def test_analysis_job_stem_failure_log_omits_dependency_payload_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parent orchestration failure logs must keep dependency details out of routine logs."""
    sensitive_detail = (
        "decode failed for /private/customer/Alice/session.wav "
        "/Users/Alice/private-song.wav token=super-secret"
    )

    def fail_features(_request: analysis_api.AnalysisJobRequest) -> None:
        raise ValueError(sensitive_detail)

    monkeypatch.setattr(analysis_api, "_build_local_audio_features", fail_features)
    caplog.set_level(logging.ERROR, logger=analysis_api.__name__)

    updates = analysis_api.run_analysis_job_updates(
        "job-privacy",
        _local_audio_request(),
        "2026-08-20T00:00:00Z",
    )

    assert updates[-1]["state"] == "failed"
    assert updates[-1]["error"] == {
        "code": "engine_unavailable",
        "message": "Stem separation failed",
    }
    assert "Stem separation failed before analysis job completion." in caplog.text
    _assert_payload_free_log(caplog)


def test_api_logger_preserves_unrelated_exception_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy redaction must not erase traceback evidence from unrelated API diagnostics."""
    caplog.set_level(logging.ERROR, logger=analysis_api.__name__)

    try:
        raise RuntimeError("non-sensitive diagnostic sentinel")
    except RuntimeError:
        analysis_api.logger.exception("Unrelated analysis API diagnostic.")

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "Unrelated analysis API diagnostic."
    ]
    assert len(records) == 1
    assert records[0].exc_info is not None
