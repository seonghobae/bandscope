"""CLI entrypoint for the bootstrap analysis orchestration flow."""

from __future__ import annotations

import json
import logging
import ntpath
import os
import stat
import sys
from datetime import UTC, datetime

from bandscope_analysis.api import get_analysis_status, run_analysis_job, run_analysis_job_updates

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_JSON_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Microsoft "Naming files, paths, and namespaces" reserved filenames.
# CONIN$/CONOUT$ are not on that list; they are console handles.
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)

# Microsoft console-handles (2021-12-30): CONIN$ and CONOUT$ are console
# input/output aliases, not reserved filenames from naming-a-file.
_WINDOWS_CONSOLE_HANDLES = frozenset({"CONIN$", "CONOUT$"})

# CLOCK$ is a legacy DOS device that modern naming-a-file no longer lists.
# Keep fail-closed so a job path cannot acquire that device.
_WINDOWS_LEGACY_DEVICE_ALIASES = frozenset({"CLOCK$"})

WINDOWS_JOB_PATH_RESERVED_FILENAME = "reserved-filename"
WINDOWS_JOB_PATH_CONSOLE_HANDLE = "console-handle"
WINDOWS_JOB_PATH_LEGACY_DEVICE = "legacy-device"
WINDOWS_JOB_PATH_DRIVE_RELATIVE = "drive-relative"
WINDOWS_JOB_PATH_UNC_OR_DEVICE_NAMESPACE = "unc-or-device-namespace"
WINDOWS_JOB_PATH_ALTERNATE_STREAM = "alternate-stream"


def failed_cli_response(message: str) -> dict[str, object]:
    """Return a typed CLI failure envelope for malformed stdin payloads."""
    requested_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "jobId": "unknown-job",
        "state": "failed",
        "requestedAt": requested_at,
        "updatedAt": requested_at,
        "error": {
            "code": "invalid_request",
            "message": message,
        },
    }


def _read_bounded_stdin() -> tuple[str | None, int]:
    """Read one bounded UTF-8 stdin payload and return text plus an exit code.

    Standard process stdin exposes ``buffer``; enforce the allocation bound on
    raw bytes before UTF-8 decoding. Text-only injected streams are already
    decoded outside this boundary, so retain a compatibility path for in-process
    callers. Failures are emitted here so callers never need to retain rejected
    payload content.
    """
    binary_stdin = getattr(sys.stdin, "buffer", None)
    if binary_stdin is None:
        input_text = sys.stdin.read(MAX_JSON_FILE_SIZE + 1)
        try:
            input_bytes = input_text.encode("utf-8")
        except UnicodeEncodeError:
            json.dump(failed_cli_response("Job input must be valid UTF-8"), sys.stdout)
            return None, 1
    else:
        input_bytes = binary_stdin.read(MAX_JSON_FILE_SIZE + 1)
    if len(input_bytes) > MAX_JSON_FILE_SIZE:
        input_source_label = "stdin"
        logger.warning(
            "Security: rejected input exceeding maximum size limit: %s",
            input_source_label,
        )
        json.dump(failed_cli_response("Job input exceeds maximum size limit"), sys.stdout)
        return None, 1
    try:
        input_text = input_bytes.decode("utf-8")
    except UnicodeDecodeError:
        json.dump(failed_cli_response("Job input must be valid UTF-8"), sys.stdout)
        return None, 1
    return input_text.strip(), 0


def _normalized_win32_device_token(path_component: str) -> str:
    """Return the Win32 device token after documented space/period normalization."""
    normalized_component = path_component.lstrip(" ").rstrip(" .")
    return normalized_component.split(".", 1)[0].rstrip(" ").split(":", 1)[0].upper()


def _path_device_tokens(job_path: str) -> list[str]:
    """Return normalized device tokens for every path component."""
    path_components = job_path.replace("\\", "/").split("/")
    return [_normalized_win32_device_token(path_component) for path_component in path_components]


def _uses_windows_reserved_filename(job_path: str) -> bool:
    """Return whether any component is a naming-a-file reserved filename."""
    return any(
        device_token in _WINDOWS_RESERVED_FILENAMES
        for device_token in _path_device_tokens(job_path)
    )


def _uses_windows_console_handle(job_path: str) -> bool:
    """Return whether any component is a CONIN$/CONOUT$ console handle."""
    return any(
        device_token in _WINDOWS_CONSOLE_HANDLES for device_token in _path_device_tokens(job_path)
    )


def _uses_windows_legacy_device_alias(job_path: str) -> bool:
    """Return whether any component is a fail-closed legacy device such as CLOCK$."""
    return any(
        device_token in _WINDOWS_LEGACY_DEVICE_ALIASES
        for device_token in _path_device_tokens(job_path)
    )


def _uses_windows_device_alias(job_path: str) -> bool:
    """Return whether any component normalizes to a reserved, console, or legacy device."""
    return (
        _uses_windows_reserved_filename(job_path)
        or _uses_windows_console_handle(job_path)
        or _uses_windows_legacy_device_alias(job_path)
    )


def _uses_windows_alternate_stream(job_path: str) -> bool:
    """Return whether a post-drive path component carries NTFS stream syntax."""
    _path_drive, drive_path_tail = ntpath.splitdrive(job_path)
    return any(
        ":" in path_component for path_component in drive_path_tail.replace("\\", "/").split("/")
    )


def classify_windows_job_path_authority(job_path: str) -> str | None:
    """Return the lexical job-path rejection class, or ``None`` if lookup may proceed.

    Classification is purely lexical and must not call ``os.lstat`` or ``os.open``.
    Console handles, including trailing-colon forms such as ``CONOUT$:``, are
    reported before the NTFS alternate-stream colon test. Reserved filenames
    and the legacy ``CLOCK$`` device follow the same order so a Win32 device
    suffix cannot be mislabeled as a named stream.
    """
    path_drive, drive_path_tail = ntpath.splitdrive(job_path)
    if job_path.replace("/", "\\").startswith("\\\\"):
        return WINDOWS_JOB_PATH_UNC_OR_DEVICE_NAMESPACE
    if path_drive and not drive_path_tail.startswith(("\\", "/")):
        return WINDOWS_JOB_PATH_DRIVE_RELATIVE
    if _uses_windows_console_handle(job_path):
        return WINDOWS_JOB_PATH_CONSOLE_HANDLE
    if _uses_windows_legacy_device_alias(job_path):
        return WINDOWS_JOB_PATH_LEGACY_DEVICE
    if _uses_windows_reserved_filename(job_path):
        return WINDOWS_JOB_PATH_RESERVED_FILENAME
    if _uses_windows_alternate_stream(job_path):
        return WINDOWS_JOB_PATH_ALTERNATE_STREAM
    return None


def _read_bounded_job_file(job_file_path: str) -> bytes:
    """Read a bounded regular local job file through a verified descriptor.

    UNC/network shapes, device namespaces, drive-relative Win32 paths, NTFS
    alternate-stream syntax, naming-a-file reserved filenames, console handles
    (``CONIN$`` / ``CONOUT$``, including ``CONOUT$:``), and the legacy ``CLOCK$``
    device are rejected lexically before any filesystem lookup. Slash
    translation is applied before the UNC/device-namespace prefix test so
    mixed-separator forms such as ``/\\\\server\\\\share`` or ``/\\\\.\\\\pipe\\\\...``
    cannot reach ``lstat`` or ``open``. Drive-relative forms such as
    ``C:job.json`` are authority-bearing: Win32 resolves them through a per-drive
    current directory rather than from the drive root. The remaining path is
    inspected with ``lstat`` before opening so known directories, FIFOs, devices,
    sockets, and symbolic links fail before descriptor acquisition. The open also
    requests nonblocking mode where available so a path replaced by a FIFO/device
    after preflight cannot turn descriptor acquisition into an unbounded wait.
    Windows opens additionally request ``O_BINARY`` so descriptor reads preserve
    the raw job bytes without text-mode CRLF or 0x1A translation. The obtained
    descriptor is then checked with ``fstat`` and must identify the same regular-file
    inode observed during preflight. ``O_NOFOLLOW`` and close-on-exec are additionally
    requested where the platform exposes them. The byte bound is enforced on the
    descriptor-backed stream rather than on a second path lookup.
    """
    path_authority = classify_windows_job_path_authority(job_file_path)
    if path_authority is not None:
        rejection_context = f"class={path_authority}"
        logger.warning("Security: rejected job path authority: %s", rejection_context)
        raise OSError("job path must use the local regular-file namespace")

    preflight_status = os.lstat(job_file_path)
    if not stat.S_ISREG(preflight_status.st_mode):
        rejection_context = "non-regular"
        logger.warning("Security: rejected non-regular job file: %s", rejection_context)
        raise OSError("job path is not a regular file")

    open_flags = os.O_RDONLY
    open_flags |= getattr(os, "O_CLOEXEC", 0)
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    open_flags |= getattr(os, "O_NONBLOCK", 0)
    open_flags |= getattr(os, "O_BINARY", 0)
    file_descriptor = os.open(job_file_path, open_flags)
    try:
        opened_status = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened_status.st_mode):
            rejection_context = "non-regular"
            logger.warning(
                "Security: descriptor yielded non-regular file: %s",
                rejection_context,
            )
            raise OSError("opened job path is not a regular file")
        if (preflight_status.st_dev, preflight_status.st_ino) != (
            opened_status.st_dev,
            opened_status.st_ino,
        ):
            rejection_context = "toctou"
            logger.warning(
                "Security: detected potential TOCTOU on job path: %s",
                rejection_context,
            )
            raise OSError("job path changed before open")
        with os.fdopen(file_descriptor, "rb", closefd=False) as job_file_stream:
            return job_file_stream.read(MAX_JSON_FILE_SIZE + 1)
    finally:
        os.close(file_descriptor)


def main() -> int:
    """Read one explicit argument or bounded stdin job and print its response."""
    progress_jsonl = "--progress-jsonl" in sys.argv[1:]
    command_arguments = [
        command_argument
        for command_argument in sys.argv[1:]
        if command_argument != "--progress-jsonl"
    ]
    job_input_data: str | None = None

    # Explicit argument modes own their input source. Resolve them before touching
    # stdin so ``--status`` and ``--job`` cannot block on an unrelated open pipe or
    # consume data that the caller did not select as the job payload.
    if command_arguments:
        if command_arguments[0] == "--status":
            if len(command_arguments) != 1:
                json.dump(
                    failed_cli_response("--status does not accept additional arguments"),
                    sys.stdout,
                )
                return 1
            json.dump(get_analysis_status(), sys.stdout)
            return 0
        if command_arguments[0] == "--job":
            if len(command_arguments) != 2:
                json.dump(
                    failed_cli_response("--job requires exactly one JSON payload or file path"),
                    sys.stdout,
                )
                return 1
            job_input_data = command_arguments[1]
            if job_input_data.lstrip(" \t\r\n").startswith("{"):
                try:
                    job_input_bytes = job_input_data.encode("utf-8")
                except UnicodeEncodeError:
                    json.dump(failed_cli_response("Job input must be valid UTF-8"), sys.stdout)
                    return 1
                if len(job_input_bytes) > MAX_JSON_FILE_SIZE:
                    rejection_context = "cli_arg"
                    logger.warning(
                        "Security: rejected oversized input: %s",
                        rejection_context,
                    )
                    json.dump(
                        failed_cli_response("Job input exceeds maximum size limit"), sys.stdout
                    )
                    return 1
            else:
                try:
                    job_input_bytes = _read_bounded_job_file(job_input_data)
                    if len(job_input_bytes) > MAX_JSON_FILE_SIZE:
                        rejection_context = "oversized-job-file"
                        logger.warning(
                            "Security: rejected oversized file: %s",
                            rejection_context,
                        )
                        json.dump(
                            failed_cli_response("Job file exceeds maximum size limit"),
                            sys.stdout,
                        )
                        return 1
                    job_input_data = job_input_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    json.dump(failed_cli_response("Job input must be valid UTF-8"), sys.stdout)
                    return 1
                except Exception:
                    json.dump(failed_cli_response("Failed to read job file"), sys.stdout)
                    return 1
        else:
            json.dump(failed_cli_response("Unsupported CLI arguments"), sys.stdout)
            return 1

    if job_input_data is None:
        job_input_data, stdin_exit_code = _read_bounded_stdin()
        if job_input_data is None:
            return stdin_exit_code

    if not job_input_data:
        json.dump(failed_cli_response("Empty input"), sys.stdout)
        return 0

    try:
        job_request_payload = json.loads(job_input_data)
    except json.JSONDecodeError as decoding_error:
        json.dump(
            failed_cli_response(f"Invalid analysis job request: {decoding_error.msg}"),
            sys.stdout,
        )
        return 0

    if not isinstance(job_request_payload, dict):
        json.dump(
            failed_cli_response("Invalid analysis job request: invalid field 'root'"), sys.stdout
        )
        return 0

    job_id = job_request_payload.get("jobId")
    if not isinstance(job_id, str) or not job_id.strip():
        json.dump(
            failed_cli_response("Invalid analysis job request: invalid field 'jobId'"), sys.stdout
        )
        return 0

    analysis_request = job_request_payload.get("request")
    requested_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if progress_jsonl:
        for job_status_update in run_analysis_job_updates(
            job_id,
            analysis_request,
            requested_at,
        ):
            json.dump(job_status_update, sys.stdout)
            sys.stdout.write("\n")
            sys.stdout.flush()
        return 0

    job_response = run_analysis_job(job_id, analysis_request, requested_at)
    json.dump(job_response, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
