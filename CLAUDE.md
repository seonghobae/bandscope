# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Canonical agent guide

`AGENTS.md` is the canonical agent operating guide — read and follow it before making changes. It defines the security workflow (`Security Notes`), supply-chain workflow, cross-platform build rules, GitHub bootstrap rules, code style, and safety guardrails. This file complements it with commands and architecture; when in doubt, `AGENTS.md` and the docs it references win.

Agent execution and delegation rules live in `docs/agents/README.md`. PR canonicalization rules live in `docs/workflow/pr-continuity.md`.

## Common commands

Setup (Node >=22.22.2 <23, Python >=3.12 via `uv`, Rust stable only for the Tauri shell):

```bash
npm install
uv sync --project services/analysis-engine --group dev
```

Primary verification — run before claiming any change complete; CI (`ci / build-and-test`) runs exactly this:

```bash
./scripts/harness/quickcheck.sh                            # lint + typecheck + test + build + doc/security/supply-chain gates
BANDSCOPE_ENABLE_RUST_CHECK=1 ./scripts/harness/quickcheck.sh  # adds the opt-in Rust/Tauri cargo check lane
```

Individual root gates (all part of quickcheck):

```bash
npm run lint        # workspace ESLint + doc/security/supply-chain checks + ruff + ruff format + bandit + docstring gate
npm run typecheck   # tsc per workspace + mypy --strict on the Python engine
npm run test        # JS workspace vitest suites + pytest with 100% coverage gate
npm run build       # vite builds per workspace
```

Per-workspace and single-test:

```bash
npm run test --workspace @bandscope/desktop                # desktop suite (vitest + coverage)
npm --workspace @bandscope/desktop exec vitest run src/lib/export.test.ts   # one frontend test file
npm run dev --workspace @bandscope/desktop                 # Vite dev server (browser fallback mode)
npm run storybook --workspace @bandscope/desktop           # component workbench

uv run --project services/analysis-engine pytest tests/test_chords.py       # one Python test file (no coverage gate)
uv run --project services/analysis-engine pytest --cov=src/bandscope_analysis --cov-report=term-missing --cov-fail-under=100   # full Python gate
```

## Architecture

BandScope is a local-first desktop app for rehearsal prep: it turns a song into likely harmony by section and role, a section roadmap, groove cues, stems, playable ranges, simplification/transposition cues, confidence flags, and rehearsal priorities. `ARCHITECTURE.md` is the authoritative reference; the analysis target is a `song -> section -> role` hierarchy, never a single song-wide chord track.

Three layers, decoupled through shared contracts:

- `apps/desktop` — Tauri 2 + Vite + React 19 shell (Tailwind 4, Base UI, Storybook). Feature screens live in `src/features/` (home, workspace, chords, ranges, player, settings). `src/lib/analysis.ts` and `src/lib/job_runner.ts` call typed Tauri IPC commands, with a browser fallback that serves demo data when not running inside Tauri.
- `apps/desktop/src-tauri/src/main.rs` — the Rust orchestration boundary. Tauri commands (`start_analysis_job`, `get_analysis_job_status`, `select_local_audio_source`, `import_youtube_url`) validate untrusted input (project IDs, file paths, URLs) and spawn the Python engine as a subprocess. There is no loopback HTTP listener and no network path for local analysis.
- `services/analysis-engine` — Python package `bandscope_analysis` (librosa/numpy). Entry point `cli.py` reads a JSON job request on stdin and prints a structured job-status JSON envelope on stdout (`--progress-jsonl` streams progress lines). `api.py` orchestrates the pipeline across the `separation`, `sections`, `roles`, `chords`, `ranges`, `temporal`, `transcription`, and `youtube` modules.

Data flow: React UI → Tauri IPC command → Rust validation + Python subprocess over stdin/stdout → job status and progress events emitted back to the UI.

Supporting packages:

- `packages/shared-types` — the stable TypeScript contracts (rehearsal domain, analysis jobs, confidence, provenance, export summaries) shared by the UI and the orchestration layer. Both sides must use these types instead of inventing parallel schemas; contracts are property-tested with fast-check.
- `packages/shared-config` — shared tsconfig/ESLint bases.
- `scripts/harness/quickcheck.sh` and `scripts/checks/` — fail-fast mechanical gates, including doc presence, plan `Security Notes`, forbidden patterns, and supply-chain/workflow-pinning verification.

## Key conventions

- Coverage is a hard gate: the Python engine requires 100% test coverage and 100% docstring coverage (Ruff `D100`–`D107` across `src`, `tests`, and repo scripts). Exported TypeScript declarations in `packages/shared-types` and `apps/desktop/src` require JSDoc with a description; `no-console` is an error.
- Gitflow: `develop` is the default branch; `feature/*` targets `develop`, `main` is the protected release branch. Direct pushes to protected branches are not allowed, and every merge needs the required checks plus a passing CodeRabbit review (see `CONTRIBUTING.md` and `docs/repository/gitflow.md`).
- The PR template (`.github/PULL_REQUEST_TEMPLATE.md`) requires a quickcheck confirmation, `Security Notes` (attack surface, trust boundary, mitigations, test points), a dependency/supply-chain checklist, and i18n impact.
- i18n: the UI ships Korean and English locales (`apps/desktop/src/locales/ko`, `en`). Any user-visible string change must update both.
- Documents under `docs/plans/` must include `Security Notes`; `scripts/checks/verify_security_notes.py` enforces this mechanically.
- Lockfiles (`package-lock.json`, `uv.lock`, `Cargo.lock`) are committed and must stay in sync; GitHub Actions are SHA-pinned. Adding a direct dependency requires the admission rationale defined in `AGENTS.md` and `docs/security/dependency-policy.md`.
- CI beyond quickcheck: `gate / ci / rust-check` (Tauri cargo check on macOS) and `build-baseline` Windows/macOS amd64+arm64 native builds are merge gates, alongside CodeQL, dependency-review, sbom, bandit, trivy, secret-scan, and security-audit workflows. Do not weaken or skip them.
- Version metadata lives in `VERSION`, the root `package.json`, and `CHANGELOG.md`; release flow is tag-driven (see `docs/operations/deploy-runbook.md`).
