# Changelog

## [Unreleased]

### Added

- Name tonight's first playable range on the ready rehearsal map and tell the player to check that span on their instrument before the section.
- Display the analyzed song tempo (BPM) as a badge in the rehearsal workspace.
- 각 합주 역할(Role)별 개인 연습 진행도를 0~100% 범위로 기록 및 시각화할 수 있는 연습 진척도(`practiceProgress`) 트래커 기능 추가. UI 컨트롤(슬라이더 및 +/- 버튼)과 한/영 다국어 지원 포함.

### Changed

- Consolidated Bandit, dependency audits, supplemental secret checks, and Trivy into one trusted-branch security backstop, delegated CodeQL to GitHub default setup, and removed duplicate local PR security and release-preflight runs.
- Pinned npm `10.9.9` as the approved lockfile generator, activated it through Node-bundled Corepack before dependency consumption, and fail closed unless its bundled `tar` is at least `7.5.19`; primary CI still consumes the committed lock only through frozen `npm ci` validation, rejects mutable npm resolution in the lock gate, requires integrity evidence for public-registry lock entries, and preserves generator-sensitive root `@esbuild/*` peer metadata.
- Replaced ambiguous private CLI job-input names with bounded-context terms and removed the retired `TemporalAnalyzer` compatibility hook after local-audio analysis moved to the orchestration API.

### Fixed

- Classify `--job` console handles (`CONIN$`/`CONOUT$`) from the 2021-12-30 console-handles contract rather than the naming-a-file reserved list, fail-close the legacy `CLOCK$` device, keep drive-relative job paths from reaching `os.lstat` or `os.open`, and log only the lexical authority class (never the rejected path).
- Reject Windows UNC/network, device-namespace, NTFS alternate-stream, and reserved Win32 device-alias `--job` file shapes before any filesystem metadata lookup, including mixed-separator UNC/device forms after `/`→`\\` translation and aliases exposed only after Win32 leading/trailing ASCII-space/period, extension, stream-suffix, and case normalization, so a caller-selected local job file cannot silently acquire remote-share, device, or named-stream authority on another host.
- Reject unknown CLI arguments and extra `--status` operands before reading standard input, keep valid whitespace-prefixed inline JSON `--job` payloads on the inline path, reject surrogate-bearing inline job arguments and text-only injected stdin with the stable UTF-8 validation error instead of terminating on an uncaught encoding exception, and fail malformed explicit invocations immediately instead of blocking on unrelated pipes or special files. File-backed `--job` reads now reject symlinks and non-regular paths before opening, request no-follow/close-on-exec/nonblocking descriptor semantics where available, verify the opened descriptor still identifies the preflighted regular file, and enforce the byte bound through that descriptor; where supported, nonblocking acquisition prevents a path swapped to a FIFO/device after preflight from turning `open()` itself into an unbounded wait.
- Upgraded the local score PDF parser to `pdfjs-dist` 6.2.108, pinned Undici 7.29.0 across the workspace, and constrained PDF loading to copied in-memory bytes with a same-origin bundled worker and npm-generated lock provenance.

## [0.1.3] - 2026-04-29

### Fixed

- Published release assets through a tag-driven draft release flow so immutable GitHub Releases include desktop installers, checksums, SBOM, and supplemental inventory before publication.
- Added a supply-chain regression guard that rejects post-publication release asset uploads.

## [0.1.2] - 2026-04-29

### Changed

- Aligned the packaged desktop app version with the release package metadata.

### Fixed

- Stabilized YouTube import fallback behavior in browser and desktop dev paths.
- Guarded OSSF Scorecard execution so release-branch pushes skip unsupported non-default branch runs cleanly.

## [0.1.1] - 2026-04-28

### Added

- Implemented rehearsal workspace design (Issue #107)
- Add capo and tuning detection heuristics (Issue #103)
- Add bandit security scan workflow

### Fixed

- Upgrade pytest to 9.0.3 to fix GHSA-6w46-j5rx-g56g
- Resolve npm audit vulnerabilities
- Fix ruff import sorting and formatting errors
- Add missing docstrings to tests
- Fix test configuration and typing issues

## [0.1.0] - 2026-03-27

### Added

- Issue #29: Defined core `song -> section -> role` rehearsal domain contracts
- Issue #38: Added cross-architecture build support (Windows/macOS arm64+amd64)
- Issue #40: Enforced 100% Python docstring and test coverage
- Issue #32: Implemented local analysis orchestration and secure IPC boundaries
- Issue #33: Implemented secure local audio intake and project bootstrap
- Issue #35: Engineered section, form, and cue anchor extraction pipeline
- Issue #34: Implemented role extraction targets and part graph
- Issue #31: Added role-specific harmony, range, overlap, and confidence metrics
- Issue #28: Delivered practical rehearsal workspace UI
- Issue #27: Supported manual overrides, provenance tracking, and local project persistence
- Issue #36: Implemented rehearsal priority calculation and cue-sheet (CSV) / chart (JSON) exports
- Issue #30: Added policy-constrained YouTube import with local fallback
- Issue #26: Finalized roadmap and prepared application for initial release

## [0.1.4] - 2026-05-15

### 추가됨 (Added)

- `ChordsFeature` (코드 분석) 화면에서 각 파트(Role)의 `transpositionPlan`(이조/조옮김 계획)을 표시하는 기능을 추가했습니다.
- `RangesFeature` (음역대 분석) 화면에서 겹침 경고(Overlap warning) 외에 해당 파트의 채보(Transcription) 가능 노드 수를 요약하여 보여주는 기능을 추가했습니다.
- 신규 UI 요소에 대한 단위 테스트를 추가했습니다 (`apps/desktop/src/features/chords/index.test.tsx`, `apps/desktop/src/features/ranges/index.test.tsx`).
