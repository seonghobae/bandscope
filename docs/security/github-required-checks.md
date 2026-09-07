# BandScope GitHub Required Checks

## Intended required checks

These are the merge-gate status checks that should be required on protected branches.

### `develop`

- `ci / build-and-test`
- `dependency-review`
- `sbom`
- `gate / build / windows`
- `gate / build / macos`
- `trivy-fs`
- `coverage-evidence`
- `opencode-review`
- `strix`
- `scan-pr-queue`
- `osv-scan`
- `scorecard`
- `Analyze (javascript-typescript)`
- `Analyze (python)`

`gate / build / windows` must cover both Windows `amd64` and Windows `arm64`.
`gate / build / macos` must cover both macOS Intel (`amd64`) and macOS `arm64`.

### `main`

- `ci / build-and-test`
- `dependency-review`
- `sbom`
- `gate / build / windows`
- `gate / build / macos`
- `trivy-fs`
- `Analyze (javascript-typescript)`
- `Analyze (python)`

The organization required-workflow rule is the authoritative PR owner for
`osv-scan`, `dependency-review`, `trivy-fs`, Scorecard visibility, Semgrep SAST,
Strix, and Noema. GitHub default setup owns CodeQL. One repository-local
`security-backstop` job combines dependency audits, Bandit, supplemental secret
checks, and Trivy after trusted-branch pushes or manual dispatch. Scorecard stays
separate because its publishing path has stricter permissions and SARIF handling.

The lists above reflect the live classic required-status contexts verified on
2026-09-04. The active organization ruleset separately requires the central
`close-empty-pr.yml`, `opencode-review.yml`, `pr-review-merge-scheduler.yml`,
`security-scan.yml`, `strix.yml`, `sast-semgrep.yml`, and `noema-review.yml`
workflows on the default branch. Keep these two enforcement mechanisms distinct
when changing local triggers. The retired local `security-audit` and
`release-preflight` PR contexts were removed from classic protection with this
workflow consolidation.

## GitHub settings baseline

These are required repository settings or GitHub security features, not branch status-check names.

- Dependabot alerts: required
- Dependabot security updates: required
- Dependency graph: required
- Dependency submission coverage: required where GitHub supports it for the repository setup
- Dependency review gate on PRs: required
- CodeRabbit review request and review-equivalent policy: required

## Workflow-managed baseline

These controls are expressed by repo workflows and are expected to be connected as intended required checks or release evidence.

- `supply-chain-inventory`: supplemental validation baseline
- `gate / build / windows`: intended required check
- `gate / build / macos`: intended required check
- per-architecture desktop artifacts: required for Windows amd64/arm64 and macOS amd64/arm64
- Windows build jobs: antivirus baseline evidence required before packaging
- release-time SBOM artifact retention: required baseline
- release-time supplemental inventory retention: required baseline

## Release evidence baseline

- CycloneDX JSON SBOM must be uploaded as a GitHub Actions artifact
- CycloneDX JSON SBOM must be attached to the GitHub Release before publication by the tag-driven draft release flow
- `supply-chain/supplemental-component-inventory.json` must be uploaded as a GitHub Actions artifact and attached to the GitHub Release before publication
- packaged desktop artifacts and checksums must remain traceable from the same release record when the release workflow emits them
- release artifacts should include explicit OS/arch naming for Windows amd64, Windows arm64, macOS amd64, and macOS arm64
- workflows must not attach assets in response to `release: published`; immutable releases reject post-publication mutation

## Enforcement note

The files in this repository define the workflows and the intended check names.
Actual branch protection, required checks, and GitHub security feature activation must be enforced in the GitHub repository settings or rulesets with repository admin permissions.

## CodeRabbit enforcement note

BandScope still requests CodeRabbit on PRs and treats it as the default AI review path.
However, the hosted `CodeRabbit` status context has shown repeated stale `PENDING` and stale `CHANGES_REQUESTED` states after all actionable review was cleared.
Because of that operational behavior, protected branches require the stable repository-owned checks above rather than the external `CodeRabbit` status context itself.

Missing repository state should trigger GitHub bootstrap per `docs/workflow/github-bootstrap-execution-policy.md`.
Only missing admin permissions or platform capability should be reported as `BLOCKED`.
