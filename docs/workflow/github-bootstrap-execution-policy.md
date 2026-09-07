# GitHub Bootstrap Execution Policy

## Purpose

For BandScope, GitHub is the source of truth for repository history, PR review, CI, Code Security, dependency checks, SBOM retention, releases, and deployment.

Because of that, missing local git state or a missing GitHub repository is not a default blocker.
It is a bootstrap condition that should trigger setup work.

## Core rule

- missing local git repo -> initialize it
- missing GitHub repo -> create it
- missing `main`, `develop`, or initial README -> bootstrap them
- missing workflows before required checks exist -> add workflows first, then tighten protections

These are setup steps, not default `BLOCKED` reasons.

## Document-then-execute rule

GitHub-related work is not complete when docs are written.
The expected sequence is:

1. summarize the intended repository and protections
2. initialize or confirm the local git repository
3. create or confirm the GitHub public repository
4. create the initial `README.md` commit to establish `main`
5. create and push `develop`
6. switch the repository default branch to `develop`
7. apply the initial branch protection baseline
8. create the bootstrap/setup branch and PR
9. add workflows and merge through PR review
10. tighten required checks once workflow names exist
11. verify with GitHub CLI or API evidence

## Supply-chain bootstrap rule

Bootstrap or setup work is not complete unless GitHub-facing supply-chain controls are both committed and, where permissions allow, enforced:

- `.github/dependabot.yml`
- `.github/workflows/security-audit.yml`
- `.github/workflows/sbom.yml`
- `.github/workflows/release.yml`
- branch protection or rulesets for `main` and `develop` that require repository CI, SBOM, platform builds, and the organization-required Security Scan, CodeQL/code-quality, SAST, Strix, and review workflows
- PR workflow that still requests CodeRabbit review and records its result when the provider responds cleanly
- release retention for the generated SBOM and supplemental inventory

Do not treat these as TODOs, later hardening, or optional recommendations.

## Bootstrap sequence

### Phase 0. Preflight

- verify `gh` exists
- verify `gh auth status`
- confirm owner and repo target
- confirm whether local `.git` exists
- confirm whether `origin` exists

### Phase 1. Local git bootstrap

- if no local git repo exists and GitHub history will matter, create the remote first and clone the repository rather than leaving the bootstrap on an ad-hoc local repo
- prepare the minimum `README.md` needed to create the first real branch on GitHub

### Phase 2. Remote public repo bootstrap

- if the public GitHub repo does not exist, create it
- connect `origin`

### Phase 3. Initial main branch creation

- push the first bootstrap commit to `main`
- this exception exists only to create the real default branch

### Phase 4. Develop branch creation

- create `develop` from `main`
- push `develop`

### Phase 4.5. Default branch handoff

- switch the repository default branch to `develop` once the integration branch exists
- keep `main` as the protected release branch even though the repository default branch is now `develop`

### Phase 5. Initial protection baseline

- apply PR-only merge
- require `CodeRabbit` as the review-equivalent gate
- disable force push
- restrict deletion
- checks can be tightened later after workflows exist

### Phase 6. Bootstrap PR

- create `bootstrap/setup` or equivalent from `develop`
- add workflows, security docs, CODEOWNERS, dependency review, SBOM, builds, and required evidence docs
- add or confirm lockfiles, dependency review, audit, SBOM, and supplemental inventory for bundled binaries and model artifacts
- merge through PR review, not direct push

### Phase 7. Tighten protections

- connect required checks once workflow names exist
- keep `main` and `develop` under PR + review + required checks
- make dependency review, audit, inventory, SBOM, and build checks actual required checks for both protected branches

### Phase 8. Verification

- verify repo existence, visibility, default branch, `develop`, protection state, required reviews, required checks, and GitHub security baseline

## Minimum GitHub evidence

When reporting a bootstrap or GitHub-enforcement task, include evidence for at least:

- the path to `.github/dependabot.yml`
- the workflow paths for CI, dependency review, security audit, code scanning, SBOM generation, and release preflight
- the required check names actually targeted for `main` and `develop`
- the SBOM format and where the Actions artifact and Release asset are retained
- the supplemental inventory path used for bundled binaries and model artifacts
- the state of Dependabot alerts, Dependabot security updates, and dependency graph or dependency submission coverage
- the exact failed command or API call if a GitHub-only enforcement step could not be completed

## Bootstrap exceptions

Direct push without PR is allowed only for:

1. the first README commit that creates the real `main` branch on GitHub
2. the first `develop` branch bootstrap push

No other substantial change should bypass PR review.

## Allowed blockers

Only use `BLOCKED` when execution is impossible due to external constraints such as:

- `gh` unavailable and not installable
- `gh auth` incomplete
- missing repo creation permission for the target owner
- missing branch protection or ruleset edit permission
- GitHub plan or permission limitations for security features
- network or auth failures to GitHub APIs

## Disallowed blockers

Do not use these as default blockers:

- local git repo missing
- GitHub repo missing
- `main` missing
- `develop` missing
- `README.md` missing
- required checks not yet existing because workflows have not been added yet

## Reporting rule

If a GitHub task is only partially completed, report:

- what bootstrap steps were executed
- what GitHub-only step remains
- the exact command or API call that failed
- the exact missing permission or external dependency
- whether the result is `FAILED` (repo-controlled artifact missing) or `BLOCKED` (GitHub permission or platform capability missing)

## Fast reference

`GitHub repository 부재나 로컬 git 부재는 BLOCKED 사유가 아니라 bootstrap 실행 조건이다.`
