# CLI job-file path authority evidence

## Status

**Active Draft PR evidence.** This record documents the security boundary under review on BandScope PR #811. It is not protected-`develop` shipped truth until the implementation is merged and revalidated on the protected branch.

## Boundary and threat model

`bandscope-analysis --job <path>` is an explicit local-file input mode. Selecting that mode authorizes one bounded read of one regular local job file; it does not grant network-share, device, pipe, directory, symlink, or alternate-data-stream authority.

A pathname is not merely a string on Windows. Universal Naming Convention (UNC) paths are used to access network resources, while DOS device paths use the `\\?\` or `\\.\` namespace forms. Windows file APIs can also translate ordinary `/` separators to `\` before native path processing. Therefore, sending an arbitrary caller-provided pathname to `os.lstat()` before classifying its namespace can acquire network or device authority even if later checks reject the resulting object (Microsoft, 2025; Microsoft, n.d.-a; Microsoft, n.d.-b).

The CLI consequently rejects pathname strings whose slash-normalized form begins with two backslashes **before any filesystem metadata lookup**. Windows file APIs translate `/` to `\\` before native path processing, so a homogeneous-separator-only prefix test would miss mixed forms such as `/\\server\\share\\job.json` and `/\\.\\pipe\\...` and would still call `os.lstat()` / `os.open()` (Microsoft, 2025; Microsoft, n.d.-a; Microsoft, n.d.-b). Normalizing `/` to `\\` first catches ordinary UNC forms, mixed-separator UNC forms, extended UNC forms such as `\\?\UNC\server\share` and `/\\?\\UNC\\...`, and device namespace forms such as `\\.\pipe\...` and `/\\.\\pipe\\...`, while making the same explicit-input contract deterministic across hosts.

NTFS also permits named alternate data streams. Microsoft documents the full stream form as `filename:stream name:stream type` and the common named-data-stream form as `file:stream`; the default stream is the one addressed when no stream-name component is supplied (Microsoft, n.d.-c; Russinovich, 2021). A caller who selected `job.json:secret` would therefore be selecting a different data stream than the ordinary file contents even though the path still names a regular filesystem object. The CLI's job-file contract intentionally authorizes only the ordinary unnamed file stream, so a colon in any post-drive path component is rejected before `os.lstat()`. The drive-designator colon in an absolute form such as `C:\path\job.json` remains distinct because `ntpath.splitdrive()` removes that authority prefix before the alternate-stream test.

Reserved Win32 device aliases are also classified before metadata lookup. Device-name comparison strips the leading ASCII space that Win32 can normalize away during file/folder creation, then applies the already-established trailing ASCII-space/period, extension, alternate-stream, and case normalization. This prevents forms such as `` NUL``, `` NUL.txt``, `` COM1 .log``, and `` AUX:`` from bypassing the lexical authority boundary merely because a caller prepended an ASCII space (Microsoft, n.d.-b).

The reserved-name list is not one bucket. Microsoft's *Naming files, paths, and namespaces* page lists `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, and `LPT1`–`LPT9`. `CONIN$` and `CONOUT$` are documented separately as console handles (Microsoft, 2021-12-30) and are not naming-a-file reserved filenames. `CLOCK$` is a legacy DOS device that the current reserved-name list no longer carries; the CLI still fail-closes it so a job path cannot acquire that device. Drive-relative forms such as `C:job.json` remain a distinct authority class and must fail before `os.lstat` or `os.open`. Rejection diagnostics log the lexical class only; they do not echo the rejected path.

## Descriptor-bound local-file validation

For a pathname that passes the lexical namespace boundary, the CLI uses this sequence:

1. call `os.lstat()` and require a regular file, rejecting directories, FIFOs, devices, sockets, and symlinks visible at preflight before `open()`;
2. open read-only, requesting close-on-exec, no-follow, and nonblocking descriptor semantics where the host exposes them;
3. call `os.fstat()` on the obtained descriptor and require a regular file whose `(st_dev, st_ino)` identity matches the preflighted file; and
4. read at most `MAX_JSON_FILE_SIZE + 1` bytes through that verified descriptor.

The nonblocking flag closes a narrower availability race that descriptor revalidation alone cannot close. A local actor can replace a preflighted regular pathname with a FIFO or blocking device between `lstat()` and `open()`. Because `fstat()` executes only after descriptor acquisition, a blocking `open(O_RDONLY)` could otherwise wait indefinitely before the authority check runs. Requesting `O_NONBLOCK` where available prevents FIFO/device acquisition from waiting for a peer, while regular-file reads retain their normal semantics; the subsequent descriptor type and inode/device checks still reject any substituted object.

Python documents `os.fstat()` as descriptor-based status inspection, `os.lstat()` as a non-following pathname status operation, and `O_NONBLOCK`, `O_NOFOLLOW`, and related flags as platform extensions that may be unavailable when the underlying C library does not define them (Python Software Foundation, 2026). The inode/device identity check is therefore retained even when these flags are available rather than treating one platform-specific flag as the complete authority boundary.

## TDD evidence contract

The current reconciliation also removes a stale CLI test seam left behind when
temporal analysis moved into the orchestration API. RED commit
`6591d34d63ad9c8344c6a43aeb4eb564d9ab96fd` requires the organization-owned
job-input boundary to use semantic identifiers and rejects the no-op
`cli.TemporalAnalyzer` hook. Production and regression callers then move
together: file-authority variables name the job path, path authority,
preflight/opened status, open flags, and descriptor explicitly, while CLI tests
observe delegation through `run_analysis_job`. The released JSON request and
response fields remain unchanged at the adapter boundary.

The first full-suite command was invoked from the repository root with the
project-relative coverage target `src/bandscope_analysis`; all 764 runnable
tests passed, but coverage correctly failed with `module-not-imported` and
`no-data-collected`. The repaired operator command changes into
`services/analysis-engine` before invoking pytest. That exact command produced
764 passes, 24 explicit native-parity skips, and 100% statement and branch
coverage. `AGENTS.md`, `CLAUDE.md`, and the harness engineering guide now encode
the working-directory invariant so the diagnostic failure is not repeated.

The original regression test landed before the namespace repair. It supplies ordinary UNC, forward-slash UNC, extended UNC, and named-pipe device paths while replacing `os.lstat()` with a sentinel that fails if any filesystem lookup is attempted. Exact-head release preflight on that RED commit failed in harness verification, establishing that the previous implementation reached the filesystem lookup. The production repair then moved namespace rejection ahead of `os.lstat()`.

A second regression-first cycle covers the `lstat()`-to-`open()` availability race. The test captures the exact descriptor flags used by `_read_bounded_job_file()` while preserving normal regular-file I/O and requires `O_NONBLOCK` whenever the host exposes it. The test-only predecessor head failed on that assertion, proving that close-on-exec/no-follow alone did not prevent a substituted FIFO/device from turning descriptor acquisition into a wait. The production repair adds only the nonblocking descriptor flag; `fstat()` regular-file and identity checks remain unchanged.

A third regression-first cycle covers Win32 leading-space normalization. The RED test replaces `os.lstat()` with a sentinel and supplies leading-space reserved aliases; therefore any failure to classify the alias lexically is observable as an attempted filesystem lookup. The production repair changes only the reserved-device normalization step by removing leading ASCII spaces before device-name comparison. It does not broadly trim arbitrary leading periods or Unicode whitespace and therefore does not widen the lexical policy beyond the documented Win32 normalization boundary.

A fourth regression-first cycle covers alternate data streams. Test-only head `6522e50ef1a4a023f4f0efb89f3f0d286d9b334b` supplies `job.json:secret`, `job.json::$DATA`, and an absolute drive path carrying a named stream while replacing `os.lstat()` with a sentinel. The production repair on its successor classifies post-drive colon syntax before filesystem lookup. The RED workflow cycle was queued when the production successor was pushed, so commit order is evidence of test-first construction but the queued predecessor run is not represented as runtime failure evidence.

A fifth regression-first cycle covers mixed-separator UNC and device-namespace forms. The RED cases `/\server\share\job.json`, `\/server\share\job.json`, `/\.\pipe\bandscope-job`, and `/\?\UNC\server\share\job.json` replace both `os.lstat()` and `os.open()` with sentinels. A homogeneous-separator-only prefix test (`startswith(("\\\\", "//"))`) lets those strings reach filesystem lookup even though `ntpath.normpath()` maps them onto UNC or `\\.\` device paths. The production repair classifies after `/`→`\\` translation and does not change accepted local absolute or relative job paths.

Commercial merge evidence still requires the final exact head to pass repository CI/release/build-baseline, owned statement and branch coverage, docstring, SAST, security, SBOM/supply-chain, and qualifying independent non-author review gates. Protected-base dependency failures owned by canonical PR #783 are not suppressed or treated as leaf-branch success.

## Residual boundary

This lexical rule deliberately does not claim to prove physical storage locality for drive-letter paths. Windows can expose storage through mounts or mappings whose network provenance is controlled outside this process. The CLI boundary prevents caller-selected UNC/device/alternate-stream namespaces from acquiring authority and verifies the selected regular file descriptor; host-level mount policy remains a deployment/endpoint-control responsibility.

Applying the Win32 alternate-stream exclusion host-independently also means a POSIX filename containing a colon is outside the portable `--job` namespace. That is an intentional portability trade-off: accepted job paths have one meaning across the supported desktop hosts rather than changing authority when a project or automation moves onto Windows.

`O_NONBLOCK` also does not claim general descriptor-level race freedom across every filesystem or operating system. It prevents the specific blocking-open availability failure where supported. Stronger race-resistant pathname acquisition primitives remain a separate platform-hardening layer when a deployment threat model includes a privileged local actor continuously replacing directory entries.

## References

Microsoft. (2024, April 23). *[MS-DFSC]: UNC path*. Microsoft Learn. https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-dfsc/149a3039-98ce-491a-9268-2f5ddef08192

Microsoft. (2025, October 22). *File path formats on Windows systems*. Microsoft Learn. https://learn.microsoft.com/en-us/dotnet/standard/io/file-path-formats

Microsoft. (n.d.-a). *Maximum path length limitation*. Microsoft Learn. Retrieved August 16, 2026, from https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation

Microsoft. (2021, December 30). *Console handles*. Microsoft Learn. https://learn.microsoft.com/en-us/windows/console/console-handles

Microsoft. (n.d.-b). *Naming files, paths, and namespaces*. Microsoft Learn. Retrieved August 16, 2026, from https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file

Microsoft. (n.d.-c). *File streams (local file systems)*. Microsoft Learn. Retrieved August 16, 2026, from https://learn.microsoft.com/en-us/windows/win32/fileio/file-streams

Python Software Foundation. (2026). *os — Miscellaneous operating system interfaces*. Python 3.14.7 documentation. https://docs.python.org/3/library/os.html

Russinovich, M. (2021, March 23). *Streams v1.6*. Microsoft Sysinternals. https://learn.microsoft.com/en-us/sysinternals/downloads/streams
