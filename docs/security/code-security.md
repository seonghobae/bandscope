# BandScope Code Security Policy

## Public GitHub baseline

BandScope treats GitHub Code Security as part of bootstrap governance.

## Required controls

- organization-required CodeQL/code-quality evidence and multi-language SAST on pull requests
- organization-required Trivy filesystem and OSV vulnerability scans
- organization-required dependency review on pull requests
- repository trusted-branch security backstop for npm, Python, and Rust dependencies in scope
- Dependabot alerts and security updates
- secret scanning in GitHub plus a supplemental trusted-branch secret check

The central Security Scan owns PR OSV, dependency-review, Trivy, and soft
Scorecard evidence. BandScope combines npm, pip, Cargo, Bandit, supplemental
secret, and Trivy checks into one trusted-branch/manual backstop. GitHub default
setup owns CodeQL, while Scorecard remains separate for its restricted publish
permissions. Central workflows own every pull-request security path.

## Enforcement

- `main` and `develop` must require the stable checks documented in `docs/repository/bootstrap-plan.md`
- Code Security controls must not be arbitrarily disabled or bypassed
- External AI-review status contexts may be requested but should not be the sole required status gate when the provider is operationally flaky.
- missing permissions to enable GitHub-native controls are `BLOCKED`, not justification to weaken the baseline
