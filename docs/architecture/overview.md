# BandScope Architecture Overview

## Product shape

BandScope is a local-first desktop app with a React + Tauri shell, shared TypeScript contracts, and a Python analysis service.

It is technically defined as a rehearsal-analysis product, not a single-output chord detector.

## Core rehearsal artifacts

- likely harmony by section and by role
- section roadmap with entries, dropouts, pickups, stops, and handoffs
- groove and timing cues
- role ranges, overlap warnings, and simplification guidance
- transposition, capo, tuning, or setup cues where relevant
- role-specific confidence and rehearsal priority

## Shared domain contracts

- Shared contracts must support a `song -> section -> role` model.
- Roles can include instruments, vocal roles, or hand-specific subdivisions when the arrangement exposes them clearly.
- Contracts should preserve user edits, provenance, and confidence so UI and exports stay aligned with the same domain model.

## Exported rehearsal deliverables

- BandScope should support cue-sheet or chart-style outputs derived from the same section and role model.
- Exported artifacts should stay compact and rehearsal-friendly rather than becoming DAW sessions or engraved scores.

## Delivery flow

GitHub is the source of truth for repository governance, PR review, CI/CD, Code Security, dependency review, SBOM retention, and release distribution.

## Local-first principle

- prefer local processing for audio and analysis
- keep risky capabilities narrow, allowlisted, and explicit
- treat files, URLs, models, caches, and release artifacts as untrusted inputs
- route orchestration through typed Tauri IPC and a narrow Python subprocess bridge before considering any loopback HTTP surface
- bootstrap local audio projects by validating the selected file in Rust, then passing only typed source metadata through the orchestration boundary
- before Python decoders transform source audio, preflight the already-open container handle through the shared `audio_resource_policy` source-rate/channel/duration contract, then rewind it for decoding
- keep project and temp/cache bootstrap roots under Tauri-resolved app-owned directories rather than the shared OS temp namespace

## CI/CD and release flow

- PRs into `develop` and `main` run repository CI, SBOM, and platform builds alongside organization-required OSV, dependency-review, Trivy, CodeQL/code-quality, Semgrep SAST, Strix, and Noema evidence; consolidated local security backstops run after trusted-branch pushes
- release flows publish desktop artifacts plus SBOM evidence to GitHub Releases through a tag-driven draft-before-publish path
- branch protection connects stable required checks after bootstrap workflows exist
