"""Tests for the analysis-engine orchestration CLI."""

from __future__ import annotations

import io
import json
import os
import runpy
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import soundfile as sf

from bandscope_analysis import cli


def run_cli(payload: object) -> Any:
    """Run the analysis CLI with a JSON payload and return its JSON response."""
    repo_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bandscope_analysis.cli",
        ],
        cwd=repo_root / "services" / "analysis-engine",
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "PYTHONPATH": str(repo_root / "services" / "analysis-engine" / "src"),
        },
    )
    return json.loads(completed.stdout)


def write_short_wav(path: Path, sample_rate: int = 8_000) -> None:
    """Write a small local-audio fixture for CLI subprocess tests."""
    samples = sample_rate // 4
    times = np.arange(samples, dtype=np.float32) / sample_rate
    mix = (0.35 * np.sin(2 * np.pi * 82.0 * times)).astype(np.float32)
    sf.write(path, mix, sample_rate)


def test_cli_returns_succeeded_job_status_for_valid_request() -> None:
    """Ensure the CLI returns a structured succeeded status for a valid request."""
    payload = {
        "jobId": "job-1",
        "request": {
            "sourceKind": "demo",
            "sourceLabel": "Late Night Set",
            "roleFocus": ["bass-guitar", "lead-vocal"],
        },
    }

    response = run_cli(payload)

    assert response["jobId"] == "job-1"
    assert response["state"] == "succeeded"
    assert cast(Any, response["result"])["title"] == "Late Night Set"


def test_cli_returns_succeeded_job_status_for_valid_local_audio_request(tmp_path) -> None:
    """Ensure the CLI accepts the local-audio intake request shape."""
    audio_path = tmp_path / "late-night-set.wav"
    write_short_wav(audio_path)
    payload = {
        "jobId": "job-local-1",
        "request": {
            "sourceKind": "local_audio",
            "projectId": "project-1",
            "sourceLabel": "late-night-set.wav",
            "roleFocus": ["bass-guitar"],
            "localSource": {
                "sourcePath": str(audio_path),
                "fileName": "late-night-set.wav",
                "extension": "wav",
                "fileSizeBytes": audio_path.stat().st_size,
            },
        },
    }

    response = run_cli(payload)

    assert response["jobId"] == "job-local-1"
    assert response["state"] == "succeeded"


def test_cli_returns_failed_status_for_invalid_request() -> None:
    """Ensure the CLI returns a typed invalid-request failure for malformed payloads."""
    response = run_cli({"jobId": "job-2", "request": {"sourceKind": "demo"}})

    assert response["jobId"] == "job-2"
    assert response["state"] == "failed"
    assert response["error"] == {
        "code": "invalid_request",
        "message": "Invalid analysis job request: invalid field 'sourceLabel'",
    }


def test_cli_returns_failed_status_for_invalid_local_audio_request() -> None:
    """Ensure malformed local-audio metadata is rejected safely."""
    response = run_cli(
        {
            "jobId": "job-local-2",
            "request": {
                "sourceKind": "local_audio",
                "projectId": "project-1",
                "sourceLabel": "late-night-set.wav",
                "roleFocus": ["bass-guitar"],
                "localSource": {
                    "sourcePath": "/Users/test/Music/late-night-set.wav",
                    "fileName": "late-night-set.wav",
                    "extension": "ogg",
                    "fileSizeBytes": 1024000,
                },
            },
        }
    )

    assert response["state"] == "failed"
    assert (
        response["error"]["message"]
        == "Invalid analysis job request: invalid field 'localSource.extension'"
    )


def test_cli_main_reads_stdin_and_writes_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the CLI entrypoint can be exercised in-process for coverage."""
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": "job-3",
                "request": {
                    "sourceKind": "demo",
                    "sourceLabel": "Late Night Set",
                    "roleFocus": ["keys-right"],
                },
            }
        )
    )
    stdout = io.StringIO()

    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    assert json.loads(stdout.getvalue())["jobId"] == "job-3"


def test_cli_main_handles_non_mapping_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the CLI handles non-dict payloads without crashing."""
    stdin = io.StringIO(json.dumps(["demo"]))
    stdout = io.StringIO()

    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    response = json.loads(stdout.getvalue())
    assert response["jobId"] == "unknown-job"
    assert response["state"] == "failed"


def test_cli_main_rejects_invalid_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure malformed job identifiers return a typed invalid-request error."""
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": 7,
                "request": {
                    "sourceKind": "demo",
                    "sourceLabel": "Late Night Set",
                    "roleFocus": ["bass-guitar"],
                },
            }
        )
    )
    stdout = io.StringIO()

    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    response = json.loads(stdout.getvalue())
    assert response["error"]["message"] == "Invalid analysis job request: invalid field 'jobId'"


def test_cli_main_handles_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure malformed JSON yields a typed invalid-request failure envelope."""
    stdin = io.StringIO("{")
    stdout = io.StringIO()

    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)

    assert cli.main() == 0
    response = json.loads(stdout.getvalue())
    assert response["jobId"] == "unknown-job"
    assert response["state"] == "failed"
    assert response["error"]["code"] == "invalid_request"
    assert (
        "Invalid analysis job request: Expecting property name enclosed in double quotes"
        in response["error"]["message"]
    )


def test_cli_module_runs_as_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the module-level main guard is covered by executing the module directly."""
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": "job-4",
                "request": {
                    "sourceKind": "demo",
                    "sourceLabel": "Late Night Set",
                    "roleFocus": ["bass-guitar"],
                },
            }
        )
    )
    stdout = io.StringIO()

    monkeypatch.setattr(sys, "argv", ["cli.py"])
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            monkeypatch.delitem(sys.modules, "bandscope_analysis.cli", raising=False)
            runpy.run_module("bandscope_analysis.cli", run_name="__main__")
    except SystemExit as exit_signal:
        assert exit_signal.code == 0

    assert json.loads(stdout.getvalue())["jobId"] == "job-4"


def test_cli_main_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure empty input yields an error."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 0
    assert "Empty input" in stdout.getvalue()


def test_cli_main_status_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure --status returns the analysis engine status."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--status"])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 0
    assert "ready" in stdout.getvalue()


def test_cli_main_job_arg_invalid_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Ensure --job with missing file yields an error."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    non_existent = tmp_path / "nope.json"
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", str(non_existent)])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 1
    assert "Failed to read job file" in stdout.getvalue()


def test_cli_main_job_arg_valid_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Ensure --job with valid file processes the job."""
    job_file = tmp_path / "job.json"
    job_file.write_text(
        json.dumps(
            {
                "jobId": "job-file",
                "request": {
                    "sourceKind": "demo",
                    "sourceLabel": "Late Night Set",
                    "roleFocus": ["keys-right"],
                },
            }
        )
    )
    stdin = io.StringIO("")
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", str(job_file)])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 0
    assert "job-file" in stdout.getvalue()


def test_cli_main_job_arg_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure --job with raw JSON string processes the job."""
    json_str = json.dumps(
        {
            "jobId": "job-raw",
            "request": {
                "sourceKind": "demo",
                "sourceLabel": "Raw String",
                "roleFocus": ["keys-right"],
            },
        }
    )
    stdin = io.StringIO("")
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", json_str])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 0
    assert "job-raw" in stdout.getvalue()


def test_cli_main_forwards_local_audio_request_to_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward validated local-audio input to the owning orchestration API."""
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": "job-audio",
                "request": {
                    "sourceKind": "local_audio",
                    "projectId": "p1",
                    "sourceLabel": "test.wav",
                    "roleFocus": [],
                    "localSource": {
                        "sourcePath": "/invalid/path.wav",
                        "fileName": "test.wav",
                        "extension": "wav",
                        "fileSizeBytes": 100,
                    },
                },
            }
        )
    )
    stdout = io.StringIO()

    observed_request: object | None = None

    def run_observed_analysis_job(
        job_id: str,
        analysis_request: object,
        requested_at: str,
    ) -> dict[str, str]:
        """Capture the validated request at the CLI-to-API boundary."""
        nonlocal observed_request
        observed_request = analysis_request
        assert job_id == "job-audio"
        assert requested_at.endswith("Z")
        return {"jobId": job_id, "state": "succeeded"}

    monkeypatch.setattr(cli, "run_analysis_job", run_observed_analysis_job)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])

    assert cli.main() == 0
    res = json.loads(stdout.getvalue())
    assert res["jobId"] == "job-audio"
    assert observed_request == json.loads(stdin.getvalue())["request"]


def test_cli_main_preserves_local_audio_orchestration_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Serialize the owning API's local-audio result without a CLI-side probe."""
    audio_path = tmp_path / "test.wav"
    write_short_wav(audio_path)
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": "job-audio-success",
                "request": {
                    "sourceKind": "local_audio",
                    "projectId": "p1",
                    "sourceLabel": "test.wav",
                    "roleFocus": [],
                    "localSource": {
                        "sourcePath": str(audio_path),
                        "fileName": "test.wav",
                        "extension": "wav",
                        "fileSizeBytes": audio_path.stat().st_size,
                    },
                },
            }
        )
    )
    stdout = io.StringIO()

    monkeypatch.setattr(
        cli,
        "run_analysis_job",
        lambda job_id, _analysis_request, _requested_at: {
            "jobId": job_id,
            "state": "succeeded",
        },
    )
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py"])

    assert cli.main() == 0
    res = json.loads(stdout.getvalue())
    assert res["jobId"] == "job-audio-success"


def test_cli_main_progress_jsonl_streams_status_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Ensure Tauri can consume incremental job status updates from stdout."""
    audio_path = tmp_path / "test.wav"
    write_short_wav(audio_path)
    stdin = io.StringIO(
        json.dumps(
            {
                "jobId": "job-progress",
                "request": {
                    "sourceKind": "local_audio",
                    "projectId": "p1",
                    "sourceLabel": "test.wav",
                    "roleFocus": [],
                    "localSource": {
                        "sourcePath": str(audio_path),
                        "fileName": "test.wav",
                        "extension": "wav",
                        "fileSizeBytes": audio_path.stat().st_size,
                    },
                    "cacheRoot": str(tmp_path / "cache"),
                    "tempRoot": str(tmp_path / "temp"),
                },
            }
        )
    )
    stdout = io.StringIO()

    monkeypatch.setattr(
        "bandscope_analysis.ranges.pitch_tracker.PitchTracker.track",
        lambda self, y, sr: None,
    )
    monkeypatch.setattr(
        "bandscope_analysis.chords.chord_recognizer.ChordRecognizer.recognize",
        lambda self, y, sr: [],
    )
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--progress-jsonl"])

    # Stem separation is real (Demucs) ML; a pipeline/progress test must not run it.
    def fake_stem_separation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        silence = np.zeros(1024, dtype=np.float32)
        return {
            "stems": {stem: silence for stem in ("vocals", "bass", "drums", "other")},
            "sample_rate": 22050,
            "duration_seconds": 1.0,
            "chunk_count": 1,
            "stem_role_types": {
                "vocals": "vocal",
                "bass": "instrument",
                "drums": "instrument",
                "other": "instrument",
            },
            "separation_notes": "mock",
        }

    monkeypatch.setattr(
        "bandscope_analysis.api._run_stem_separation_with_timeout",
        fake_stem_separation,
    )

    assert cli.main() == 0
    updates = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [update["progressStage"] for update in updates] == [
        "decode",
        "separate",
        "analyze",
        "persist",
        "ready",
    ]
    assert updates[-1]["state"] == "succeeded"
    assert updates[-1]["progressPercent"] == 100


def test_cli_main_job_arg_rejects_large_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Ensure --job rejects files larger than MAX_JSON_FILE_SIZE."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    job_file = tmp_path / "large_job.json"

    # Create a dummy file larger than MAX_JSON_FILE_SIZE
    from bandscope_analysis.cli import MAX_JSON_FILE_SIZE

    with open(job_file, "wb") as f:
        f.seek(MAX_JSON_FILE_SIZE + 1024)
        f.write(b"0")

    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "--job", str(job_file)])
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main() == 1
    assert "Job file exceeds maximum size limit" in stdout.getvalue()
