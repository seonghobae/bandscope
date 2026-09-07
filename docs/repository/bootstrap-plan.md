# BandScope Bootstrap Plan

## Required execution order

1. Create the public GitHub repository with initial default branch `main`.
2. Push a one-time empty `README.md` commit directly to `main`.
3. Create `develop` from `main`.
4. Switch the repository default branch to `develop` after `develop` exists.
5. Apply phase-1 protection to `main` and `develop` without required checks yet.
6. Create `bootstrap/setup` from `develop`.
7. Add the bootstrap baseline files, workflows, templates, docs, i18n seed files, and app skeleton.
8. Open `bootstrap/setup -> develop` and assign a reviewer.
9. After merge, tighten protections by connecting required checks.
10. Open `develop -> main` as `release/bootstrap-initial`.

## Phase-1 protections

- direct push blocked
- PR required
- passing `CodeRabbit` gate required
- conversation resolution required
- force push blocked
- branch deletion blocked
- admins included
- bypass list empty

## Phase-2 protections

After workflows exist, require these stable checks on `main` and `develop`:

- `CodeRabbit`
- `ci / build-and-test`
- `dependency-review`
- `sbom`
- `gate / build / windows`
- `gate / build / macos`
- `trivy-fs`
- `Analyze (javascript-typescript)`
- `Analyze (python)`
- organization-required Security Scan, CodeQL/code-quality, SAST Semgrep, Strix, Noema, OpenCode, scheduler, and empty-PR workflows

## Initial README exception

The empty `README.md` commit exists only to initialize the public repository before protections can be enforced. It is not a standing exception.

## Default branch declaration

After bootstrap creates `develop`, the repository default branch is `develop`. `main` remains the protected release branch.

## Review substitution rule

For this harness baseline, a passing `CodeRabbit` check replaces GitHub's built-in approving-review gate. Protected branches still require PRs, conversation resolution, and all required checks.

## Path note

The current harness uses `services/analysis-engine` as the effective analysis service root. That path is treated as the concrete implementation of the requested Python analysis baseline.

## Docs or Pages note

`docs-or-pages.yml` is intentionally omitted from the baseline because the repository does not yet publish end-user documentation. The omission is deliberate rather than deferred; if public docs hosting becomes required, add a dedicated workflow and update this plan.
