# Harness Engineering Guide

## Purpose

Capture repository-local harness and verification behavior used for engineering acceptance.

## Primary local entrypoint

- `./scripts/harness/quickcheck.sh`

Quickcheck aggregates lint/type/test/build and repository policy checks intended to mirror CI baseline safety.

## Core verification commands

- Lint and policy checks: `npm run lint`
- Type checks: `npm run typecheck`
- Tests: `npm run test`
- Workspace vulnerability gate: `npm audit --workspaces --audit-level=high`

## Supply-chain and workflow policy checks

- `python3 scripts/checks/verify_supply_chain.py`
- `python3 scripts/checks/security_gates.py`
- `python3 scripts/checks/verify_github_bootstrap_policy.py`

## Python analysis engine notes

- Dependency sync: `uv sync --project services/analysis-engine --group dev`
- Tests: `(cd services/analysis-engine && uv run --project . --group dev pytest --cov=src/bandscope_analysis --cov-report=term-missing --cov-fail-under=100)`

## CI parity expectation

Local verification should be chosen to match touched areas and must not undercut protected-branch required checks documented in `docs/security/github-required-checks.md`.
