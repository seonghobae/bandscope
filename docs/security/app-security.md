# BandScope App Security

## Purpose

This document is the security source of truth for BandScope.
Future agents must apply it when writing PRDs, TRDs, UX flows, code, exports, logs, import features, local backend contracts, and model delivery behavior.

## App security context

BandScope is a Windows/macOS local-first desktop app.
Users provide local audio files or YouTube URLs, and the product performs rehearsal analysis: per-role harmony detection, stem separation, form and cue extraction, range visualization, loop playback, confidence-marked guidance, project save/load, and result export.

BandScope should not become a product that trades away safety for convenience.
Even as a local desktop app, it still exposes attack surfaces through files, decoders, subprocesses, model artifacts, WebView rendering, IPC, local backend communication, updates, exports, logs, caches, and installers.

This security context is not meant to block useful product work.
It exists so the default answer is secure-by-default rather than convenient-but-risky.

## Security posture

- local-first
- minimum privilege
- untrusted-input by default
- allowlist over generic capability
- safe failure over risky convenience

## Security goals

- minimum privilege for file, folder, network, process, IPC, and OS access
- untrusted-input handling across files, URLs, metadata, project files, model files, and export paths
- safe defaults without hidden risky behavior
- local-first analysis with minimal network dependency
- separation of privilege across UI, analysis, subprocess, model delivery, and update paths
- honest failure instead of risky workaround suggestions
- transparency about what is stored, where it is stored, and how long it stays

## Trust boundaries

Treat all of the following as untrusted input:

- local audio files
- YouTube URLs and metadata
- drag-and-drop payloads
- imported project files
- export target names and paths
- model manifests and model files
- cache contents and temp files
- local backend request payloads
- IPC messages

### Named boundaries

- `User Input Boundary` - local files, URLs, drag-and-drop payloads, imported projects, edited metadata
- `Process Boundary` - desktop UI, Python engine, ffmpeg or other native tools, updater, model downloader
- `IPC Boundary` - frontend to native shell to Python engine to local service
- `Storage Boundary` - original files, temp files, caches, stems, logs, project database, exports
- `Network Boundary` - YouTube access, update checks, model downloads, optional diagnostics

Every boundary crossing requires validation, scope restriction, minimal logging, and an explicit allowlist or schema.

## Non-goals

- do not add generic downloader behavior for arbitrary sites
- do not add convenience-first debug surfaces that bypass validation or allowlists
- do not justify risky defaults as temporary developer shortcuts

## Required rules

### Files and paths

- Normalize paths before use.
- Defend against path traversal and parent directory escape.
- Restrict temp and cache files to app-owned directories.
- Add cleanup policies for temp and cache content.
- Never expose generic read/write APIs that can touch arbitrary paths.
- Do not log the full original file path unless explicitly redacted or truncated for safety.

### URLs and imports

- Accept only supported URL classes and validate them strictly.
- Do not support DRM bypass, login bypass, or region restriction bypass for YouTube imports.
- Do not silently follow dangerous redirects or downgrade trust.
- Treat remote metadata as untrusted and schema-validate it.
- Fail with a safe fallback when a remote URL cannot be handled legally or safely.

### Subprocesses

- Use `shell=False` and argument arrays only.
- Do not build stringly typed shell commands.
- Do not add generic exec wrappers.
- Keep subprocesses on an allowlist by executable and expected argument shape.

### IPC and local backend

- Bind local backend only to `127.0.0.1` when a local HTTP surface exists.
- Prefer direct IPC over a wider local HTTP surface when possible.
- Allow only explicitly allowlisted IPC commands.
- Validate all IPC and local backend payloads against strict schemas.
- Reject unknown commands, unknown fields, and malformed payloads by default.
- If a local HTTP service exists, consider per-session tokens or equivalent anti-cross-process protection.
- For the current orchestration slice, prefer stdin/stdout JSON exchange with an allowlisted Python subprocess over opening a new local HTTP listener.

### WebView and UI rendering

- Do not inject untrusted HTML into the WebView.
- Render untrusted content as text, not HTML.
- Avoid dynamic capabilities that widen the Tauri shell without a documented need.

### Models, updates, and binaries

- Download only from verified sources.
- Require checksum or signature validation for model, binary, and update artifacts.
- Do not load untrusted `pickle` or equivalent code-executing artifacts as-is.
- Prefer safer serialization or explicit trusted manifests.
- Prefer pinned versions and reproducible installs.

### Logging and privacy

- Do not log raw audio.
- Do not log full URLs, tokens, cookies, or secrets.
- Do not log whole project payloads if a summary is enough.
- Keep logs useful for debugging but redacted by default.

### Export safety

- Prevent CSV formula injection.
- Sanitize export filenames and derived metadata.
- Keep export scope narrow and predictable.
- Treat cue sheets, chart-style exports, lyric-linked anchors, and role summaries as derived data that still require sanitization.
- Do not let export formats expand into arbitrary scriptable project formats or unsafe document payloads.

## Feature-specific rules

### Local file import

- Treat local media as potentially malformed or malicious.
- Cross-check extension, MIME, and actual decode behavior.
- Prefer isolated worker processing for decode and analysis.
- Guard against very large files, abnormal duration, and hostile metadata.
- Apply the versioned canonical local-audio resource policy consistently at request preflight and again at the opened-file/decoded-waveform boundary; request metadata is never authoritative for actual resource use.
- Before any decoder resamples, downmixes, or duration-truncates local audio, inspect source-container metadata from the already-open handle with `soundfile.info`, enforce the shared 8 kHz–192 kHz and mono/stereo source contract, reject overlong sources, and rewind the handle before `librosa.load`.
- In the Python analysis boundary, reject decoded audio that is empty, non-finite, wrong-rate, wrong-shaped, or over the accepted sample budget before beat tracking or model inference. Use the one-sample-over decode probe described in `docs/doctoring/audio-resource-policy.md` so an exact-boundary track remains accepted while excess decoded output is observable and fails closed.
- Do not add arbitrary filesystem scanning just to find media files.
- When bootstrapping a project around local audio, use the OS-selected external file only as untrusted admission input. Stage and sync admitted bytes under the app-owned project root, publish them as `source.<extension>`, then reopen and verify the published regular/non-symlink object against the bounded size and SHA-256 receipt before analysis or persistence. Do not persist an arbitrary external absolute path as authority.

### YouTube and remote URL import

- Support only publicly and legally accessible import paths.
- Do not design or recommend DRM bypass, login bypass, cookie theft, geo bypass, paywall bypass, or anti-bot evasion.
- Validate scheme, host, path, and query before any fetch or handoff.
- Do not widen URL intake into a generic remote downloader.
- Sanitize remote metadata before display.
- Apply the same canonical 100 MiB encoded-byte ceiling during YouTube download as local-file intake. Abort with yt-dlp `max_filesize` and a progress hook, then delete owned `.part` / `.ytdl` / `-Frag*` siblings that stay inside that import directory. Do not keep a divergent post-download-only 50 MB limit that lets a large transfer fill the cache root first.
- Revalidate the filesystem-observed downloaded length before storing bootstrap state. Treat announced `filesize` / `filesize_approx` as a pre-download hint only.

### Subprocesses and native tools

- Use fixed command templates plus allowlisted arguments.
- Apply timeout, output path restriction, and resource bounds where possible.
- Redact sensitive paths and tokens from surfaced stderr or stdout.
- Track tool versions and their supply chain source.

### Analysis engine and Python boundary

- Treat the Python engine as a separate trust boundary.
- Prefer direct IPC, named pipes, or domain sockets over a broad local HTTP service when feasible.
- If local HTTP is used, bind to `127.0.0.1` only and validate payloads with a strict schema layer.
- Do not open permissive CORS or generic file endpoints.

### Model artifacts

- Do not load user-supplied `pickle`, `joblib`, or `torch checkpoint` files as trusted models.
- Prefer `safetensors`, ONNX, or other lower-risk formats plus a verified manifest.
- Require HTTPS plus checksum or signature validation and version pinning for downloads.

### Storage, cache, and temp files

- Keep cache and temp output under app-owned directories.
- Document retention and cleanup policy.
- Set restrictive permissions where the platform allows it.
- Tell the user whether a project references the original file or copies it.
- For bootstrap local-audio projects, resolve project and cache/temp roots from app-owned Tauri data/cache paths rather than the shared system temp namespace.

### Logging, telemetry, and crash reports

- Do not log raw audio, full URLs, tokens, cookies, or local usernames.
- Keep debug logging opt-in or development-only.
- Require explicit consent for crash uploads or diagnostics transmission.

### UI rendering

- Do not render file names, artist names, video titles, or project notes as trusted HTML.
- Sanitize markdown rendering by default.
- Keep clipboard and copy actions scoped to what the user expects.

### Secrets and credentials

- Do not hardcode API keys, cookies, or tokens in code or repo docs.
- Use OS credential stores when secret storage is required.
- Do not assume user credentials as the default path for YouTube import.

### Updater, installer, and release

- Prefer signed update and installer paths.
- Keep developer convenience settings out of release builds.
- Separate development-only tooling from release delivery configuration.

### Dependencies and supply chain

- Use only necessary packages.
- Commit lockfiles and prefer pinned versions.
- Document provenance of ffmpeg bundles, native dependencies, and model artifacts.
- Avoid untrusted postinstall scripts and packages with unnecessary privilege.

## Desktop hardening rules

### Tauri

- Allow only required plugins and scopes.
- Keep filesystem, network, and shell scopes minimal.
- Validate command handler payloads with explicit types.
- Do not load remote content into a privileged Tauri context.

### Electron

- Keep `nodeIntegration` off.
- Keep `contextIsolation` on.
- Keep `sandbox` on.
- Expose only allowlisted APIs through `contextBridge`.
- Restrict `shell.openExternal` to allowed schemes and hosts.

### Local backend

- Do not bind to external interfaces.
- Do not expose unauthenticated generic endpoints.
- Keep file read, write, and execution actions use-case-specific and allowlisted.

## Security Notes requirement

If a task touches any of the following, it must include a `Security Notes` section in the design doc, plan, or user-facing implementation summary:

- file import or export
- URL intake
- subprocess execution
- IPC or local backend contracts
- WebView rendering
- model download or loading
- update delivery
- cache or temp file handling
- project file format changes
- logging, telemetry, or crash report changes
- cloud integrations or account features

`Security Notes` should explain:

- attack surface
- trust boundary touched
- realistic threats
- mitigations
- remaining risk
- test points

## Required security deliverables

When a design or implementation plan materially touches a risky boundary, provide:

### Threat model

- likely abuse scenarios
- highest impact scenarios
- most realistic attacker paths

### Data classification

Classify at least:

- original audio
- temporary cache
- separated stems
- project files
- logs
- diagnostics

Document sensitivity, storage location, retention, and sharing policy.

### Security requirements

- input validation
- privilege scope
- storage policy
- network policy
- update policy
- logging and privacy policy

### Security acceptance criteria

- explicit checks required for completion

### Security test plan

- path traversal rejection
- shell injection rejection
- malformed media handling
- malicious metadata rendering tests
- CSV injection prevention
- unauthorized IPC rejection
- signature or hash mismatch rejection
- temp file cleanup behavior

## Agent decision rules

- Prefer the safer default when the alternative is stronger but riskier.
- Prefer allowlisted capabilities over generic ones.
- Rework automation if it requires excessive privilege.
- Prefer release-grade safety over debug convenience.
- Do not trade away validation or boundary separation for performance alone.
- Do not break UX for security, but do not justify risky bypasses in the name of UX.
- Do not excuse `shell exec`, wide-open CORS, unrestricted file access, or unsigned downloads as temporary shortcuts.

## Mechanical review prompts

When reviewing a change, ask:

- Does this add a generic capability instead of a narrow allowlisted one?
- Does this trust a file, URL, model, or metadata field too early?
- Does this log more than needed?
- Does this widen local attack surface without a clear reason?
- Does this make the easy path safer by default?

## Mechanical checks

- `scripts/checks/verify_docs.py` ensures the security source documents stay present.
- `scripts/checks/verify_security_notes.py` ensures plan docs include `Security Notes` with required subsections.
- `scripts/checks/security_gates.py` catches basic forbidden patterns such as `shell=True`, stringly subprocess commands, `pickle`-style artifact loading, remote script piping, and unsafe HTML insertion markers.

## Fast reference

Evaluate every new design and feature by this line:

`권한 최소화, 입력 불신, 로컬 우선, 안전한 실패`

Short summary:

`모든 입력을 불신하고, 권한을 최소화하며, 로컬 우선으로 처리하고, 위험한 우회 없이 안전한 기본값으로 동작하는 데스크톱 앱을 설계하라.`
