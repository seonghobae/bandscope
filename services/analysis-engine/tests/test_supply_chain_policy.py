"""Tests for repository supply-chain and workflow coverage checks."""

from __future__ import annotations

import importlib
import json
import re
import stat
import zipfile
from pathlib import Path

import pytest
from conftest import load_module, make_symlink_or_skip


def central_required_workflow_policy_text() -> str:
    """Return the repository policy text that delegates review automation centrally."""
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / "docs" / "workflow" / "pr-review-merge-scheduler.md").read_text(
        encoding="utf-8"
    )


def assert_local_review_workflows_removed() -> None:
    """Ensure this repository does not carry local copies of central review workflows."""
    repo_root = Path(__file__).resolve().parents[3]
    assert not (repo_root / ".github" / "workflows" / "opencode-review.yml").exists()
    assert not (repo_root / ".github" / "workflows" / "pr-review-merge-scheduler.yml").exists()
    for helper in (
        "classify_failed_check_evidence.py",
        "collect_failed_check_evidence.sh",
        "emit_opencode_failed_check_fallback_findings.sh",
        "opencode_review_approve_gate.sh",
        "opencode_review_normalize_output.py",
        "pr_review_merge_scheduler.py",
        "validate_opencode_failed_check_review.sh",
    ):
        assert not (repo_root / "scripts" / "ci" / helper).exists()


def test_supply_chain_check_requires_multi_arch_runner_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure missing multi-arch workflow tokens are reported as violations."""
    supply_chain = load_module("scripts/checks/verify_supply_chain.py", "verify_supply_chain")

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build-windows:
    runs-on: windows-latest
  build-macos:
    runs-on: macos-latest
""".strip(),
        encoding="utf-8",
    )
    for path in supply_chain.REQUIRED_FILES:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("placeholder", encoding="utf-8")
    (tmp_path / ".github" / "dependabot.yml").write_text(
        "\n".join(
            [
                'package-ecosystem: "npm"',
                'package-ecosystem: "pip"',
                'package-ecosystem: "cargo"',
                'package-ecosystem: "github-actions"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert "build workflow missing token: windows-11-arm" in violations
    assert "build workflow missing token: macos-15-intel" in violations
    assert "build workflow missing token: bandscope-windows-arm64-${{ github.sha }}" in violations
    assert "build workflow missing token: bandscope-macos-amd64-${{ github.sha }}" in violations
    assert "build workflow missing token: Get-MpComputerStatus" in violations


def test_supply_chain_check_accepts_repo_multi_arch_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the checked-in multi-arch workflow satisfies the baseline policy."""
    supply_chain = load_module("scripts/checks/verify_supply_chain.py", "verify_supply_chain_repo")
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("build workflow missing token" in violation for violation in violations)
    assert (
        "build workflow should not rely on windows-latest for architecture coverage"
        not in violations
    )
    assert (
        "build workflow should not rely on macos-latest for architecture coverage" not in violations
    )


def test_build_baseline_upload_artifact_pins_are_consistent() -> None:
    """Ensure all upload-artifact steps use the same reviewed SHA pin."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "build-baseline.yml").read_text(
        encoding="utf-8"
    )
    pins = re.findall(r"actions/upload-artifact@([A-Fa-f0-9]{40})", workflow)

    assert pins
    assert len(set(pins)) == 1


def test_windows_antivirus_probe_logs_defender_provider_failures() -> None:
    """Ensure hosted-runner Defender provider errors do not fail Windows builds."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "build-baseline.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count("Get-MpComputerStatus -ErrorAction Stop") == 2
    assert workflow.count("Antivirus check: Defender telemetry query failed") == 2
    assert workflow.count("$products = Get-CimInstance -Namespace root/SecurityCenter2") == 2
    assert workflow.count("$defenderService = Get-Service -Name WinDefend") == 2


def test_supply_chain_check_requires_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure checkout workflows suppress Git initial-branch warnings at source."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_checkout_default_branch_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == [
        ".github/workflows/ci.yml: workflows using actions/checkout must set "
        "workflow-level GIT_CONFIG_* init.defaultBranch env to avoid Git "
        "initial-branch warnings"
    ]


def test_supply_chain_check_rejects_commented_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure commented guard examples do not satisfy the checkout warning guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_commented_checkout_default_branch_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
# env:
#   GIT_CONFIG_COUNT: "1"
#   GIT_CONFIG_KEY_0: init.defaultBranch
#   GIT_CONFIG_VALUE_0: develop
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == [
        ".github/workflows/ci.yml: workflows using actions/checkout must set "
        "workflow-level GIT_CONFIG_* init.defaultBranch env to avoid Git "
        "initial-branch warnings"
    ]


def test_supply_chain_check_ignores_commented_checkout_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure commented checkout references do not trigger guard enforcement."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_commented_checkout_reference",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
# - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - run: node --version
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_supply_chain_check_ignores_run_step_checkout_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure run-step checkout text does not trigger guard enforcement."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_run_step_checkout_reference",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_supply_chain_check_rejects_run_step_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure later shell text does not satisfy the checkout warning guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_run_step_checkout_default_branch_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
      - run: |
          GIT_CONFIG_COUNT: "1"
          GIT_CONFIG_KEY_0: init.defaultBranch
          GIT_CONFIG_VALUE_0: develop
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == [
        ".github/workflows/ci.yml: workflows using actions/checkout must set "
        "workflow-level GIT_CONFIG_* init.defaultBranch env to avoid Git "
        "initial-branch warnings"
    ]


def test_supply_chain_check_rejects_nested_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure a single nested env block cannot satisfy the workflow guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_nested_checkout_default_branch_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
jobs:
  guarded:
    runs-on: ubuntu-latest
    env:
      GIT_CONFIG_COUNT: "1"
      GIT_CONFIG_KEY_0: init.defaultBranch
      GIT_CONFIG_VALUE_0: develop
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
  unguarded:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == [
        ".github/workflows/ci.yml: workflows using actions/checkout must set "
        "workflow-level GIT_CONFIG_* init.defaultBranch env to avoid Git "
        "initial-branch warnings"
    ]


def test_supply_chain_check_rejects_top_level_nested_env_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure nested top-level env maps cannot satisfy the checkout warning guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_top_level_nested_env_checkout_default_branch_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
env:
  CONFIGS:
    GIT_CONFIG_COUNT: "1"
    GIT_CONFIG_KEY_0: init.defaultBranch
    GIT_CONFIG_VALUE_0: develop
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == [
        ".github/workflows/ci.yml: workflows using actions/checkout must set "
        "workflow-level GIT_CONFIG_* init.defaultBranch env to avoid Git "
        "initial-branch warnings"
    ]


def test_supply_chain_check_accepts_checkout_default_branch_guard_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure top-level env comments do not break valid checkout warning guards."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_checkout_default_branch_guard_comments",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: ci
env: # Git subprocess defaults inherited by actions/checkout.
  GIT_CONFIG_COUNT: "1" # one key/value pair follows
  GIT_CONFIG_KEY_0: init.defaultBranch
  GIT_CONFIG_VALUE_0: develop
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_supply_chain_check_accepts_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in checkout workflows carry the warning guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_repo_checkout_default_branch_guard",
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_supply_chain_check_accepts_scorecard_step_level_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard can avoid global env while still guarding checkout."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_scorecard_step_level_checkout_default_branch_guard",
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
jobs:
  analysis:
    name: ossf-scorecard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        env:
          GIT_CONFIG_COUNT: "1"
          GIT_CONFIG_KEY_0: init.defaultBranch
          GIT_CONFIG_VALUE_0: develop
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
        if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
        with:
          publish_results: PUBLISH_GUARD
  scorecard-sarif-upload:
    name: scorecard-sarif-upload
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        env:
          GIT_CONFIG_COUNT: "1"
          GIT_CONFIG_KEY_0: init.defaultBranch
          GIT_CONFIG_VALUE_0: develop
""".strip().replace("PUBLISH_GUARD", publish_guard),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_supply_chain_check_rejects_scorecard_missing_checkout_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard checkout steps require the step-level Git guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_scorecard_missing_checkout_default_branch_guard",
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
jobs:
  analysis:
    name: ossf-scorecard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
        if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
        with:
          publish_results: PUBLISH_GUARD
""".strip().replace("PUBLISH_GUARD", publish_guard),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert any(
        supply_chain.OSSF_CHECKOUT_DEFAULT_BRANCH_GUARD_VIOLATION in violation
        for violation in violations
    )


def test_supply_chain_check_ignores_scorecard_publish_mentions_in_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure comment text does not make a workflow look like Scorecard publish."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_scorecard_publish_comment_mentions",
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "checkout.yml").write_text(
        """
name: checkout
on:
  pull_request:
env:
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: init.defaultBranch
  GIT_CONFIG_VALUE_0: develop
jobs:
  checkout:
    runs-on: ubuntu-latest
    steps:
      # uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a
      # publish_results: true
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_checkout_default_branch_guard()

    assert violations == []


def test_python_security_audit_does_not_ignore_patched_pygments_advisory() -> None:
    """Ensure patched Python advisories are not left as stale audit ignores."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "security-audit.yml").read_text(
        encoding="utf-8"
    )
    dependency_policy = (repo_root / "docs" / "security" / "dependency-policy.md").read_text(
        encoding="utf-8"
    )
    python_lockfile = (repo_root / "services" / "analysis-engine" / "uv.lock").read_text(
        encoding="utf-8"
    )

    assert "--ignore-vuln GHSA-5239-wwwm-4pmq" not in workflow
    assert "uv run --project services/analysis-engine --with pip-audit==2.8.0" in workflow
    assert "pip-audit --local --strict" in workflow
    assert "Pygments <2.20.0" in dependency_policy
    assert "pip-audit --local --strict" in dependency_policy
    tomllib = importlib.import_module("tomllib")
    lock = tomllib.loads(python_lockfile)
    packages = lock.get("package", [])
    pygments = [package for package in packages if package.get("name") == "pygments"]

    assert len(pygments) == 1
    assert pygments[0].get("version") == "2.20.0"
    assert all(package.get("version") != "2.19.2" for package in pygments)


def test_python_lockfile_keeps_msgpack_at_patched_advisory_version() -> None:
    """Ensure Trivy's msgpack crash advisory cannot re-enter the Python lockfile."""
    repo_root = Path(__file__).resolve().parents[3]
    python_lockfile = (repo_root / "services" / "analysis-engine" / "uv.lock").read_text(
        encoding="utf-8"
    )

    tomllib = importlib.import_module("tomllib")
    lock = tomllib.loads(python_lockfile)
    packages = lock.get("package", [])
    msgpack = [package for package in packages if package.get("name") == "msgpack"]

    assert len(msgpack) == 1
    assert msgpack[0].get("version") == "1.2.1"


def test_security_audit_workflow_keeps_dependency_vulnerability_scans() -> None:
    """Ensure the audit workflow keeps npm, Python, and Rust vulnerability scans."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "security-audit.yml").read_text(
        encoding="utf-8"
    )

    assert "npm audit --workspaces --audit-level=high" in workflow
    assert "pip-audit --local --strict" in workflow
    assert "cargo +stable audit" in workflow


def test_supply_chain_check_requires_audit_tokens_in_run_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure comments and env values cannot satisfy vulnerability scan coverage."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_audit_run_steps",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
env:
  AUDIT_EXAMPLES: npm audit --workspaces --audit-level=high
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Non-executed audit examples
        run: |
          true # npm audit --workspaces --audit-level=high
          # pip-audit --local --strict
          printf '%s\n' "cargo +stable audit"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "security audit workflow missing vulnerability audit token: "
        "npm audit --workspaces --audit-level=high"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: pip-audit --local --strict"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: cargo +stable audit"
    ) in violations


def test_supply_chain_check_accepts_nested_shell_audit_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure shell -c wrappers cannot hide real vulnerability scan commands."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_nested_shell_audit",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Nested npm audit
        run: bash --norc -lc 'npm audit --workspaces --audit-level=high'
      - name: Nested Python audit
        run: sh -ec 'pip-audit --local --strict'
      - name: Nested Rust audit
        run: /bin/bash -c 'cargo +stable audit'
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("missing vulnerability audit token" in item for item in violations)


def test_supply_chain_check_rejects_noop_audit_command_spoofs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure shell no-op commands cannot satisfy vulnerability audit coverage."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_noop_audit_spoof",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Spoof npm audit
        run: : npm audit --workspaces --audit-level=high
      - name: Spoof Python audit
        run: : pip-audit --local --strict
      - name: Spoof Rust audit
        run: : cargo +stable audit
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "security audit workflow missing vulnerability audit token: "
        "npm audit --workspaces --audit-level=high"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: pip-audit --local --strict"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: cargo +stable audit"
    ) in violations


def test_supply_chain_check_requires_blocking_audit_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure continue-on-error audit steps cannot satisfy vulnerability coverage."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_blocking_audit",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Non-blocking npm audit
        continue-on-error: true
        run: npm audit --workspaces --audit-level=high
      - name: Non-blocking Python audit
        continue-on-error: true
        run: pip-audit --local --strict
      - name: Non-blocking Rust audit
        continue-on-error: true
        run: cargo +stable audit
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "security audit workflow missing vulnerability audit token: "
        "npm audit --workspaces --audit-level=high"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: pip-audit --local --strict"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: cargo +stable audit"
    ) in violations


def test_supply_chain_check_requires_unconditional_audit_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure conditional audit steps cannot satisfy vulnerability coverage."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_unconditional_audit",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Skipped npm audit
        if: ${{ false }}
        run: npm audit --workspaces --audit-level=high
      - name: Skipped Python audit
        if: false
        run: pip-audit --local --strict
      - name: Skipped Rust audit
        if: github.ref == 'refs/heads/not-used'
        run: cargo +stable audit
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "security audit workflow missing vulnerability audit token: "
        "npm audit --workspaces --audit-level=high"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: pip-audit --local --strict"
    ) in violations
    assert (
        "security audit workflow missing vulnerability audit token: cargo +stable audit"
    ) in violations


def test_supply_chain_check_accepts_explicit_false_continue_on_error_audit_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure explicitly blocking audit steps still satisfy coverage."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_explicit_false_continue_on_error",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "security-audit.yml").write_text(
        """
name: security-audit
on:
  pull_request:
  push:
    branches: [develop, main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Blocking npm audit
        continue-on-error: false
        run: npm audit --workspaces --audit-level=high
      - name: Blocking Python audit
        continue-on-error: "false"
        run: pip-audit --local --strict
      - name: Blocking Rust audit
        continue-on-error: ${{ false }}
        run: cargo +stable audit
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("missing vulnerability audit token" in item for item in violations)


def test_supply_chain_check_requires_ossf_default_branch_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure OSSF Scorecard is not invoked on non-default release branches."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_guard"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
      - main
  schedule:
    - cron: '30 1 * * 1'
jobs:
  analysis:
    name: ossf-scorecard
    steps:
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard workflow must guard Scorecard execution to the repository default branch"
        in violations
    )


def test_supply_chain_check_requires_ossf_guard_without_main_branch_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard guard validation cannot be bypassed by omitting main."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_guard_no_main"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
  schedule:
    - cron: '30 1 * * 1'
jobs:
  analysis:
    name: ossf-scorecard
    steps:
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard workflow must guard Scorecard execution to the repository default branch"
        in violations
    )


def test_supply_chain_check_rejects_hardcoded_ossf_publish_results_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard publish settings follow the repository default branch."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_publish"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
      - main
  schedule:
    - cron: '30 1 * * 1'
jobs:
  analysis:
    name: ossf-scorecard
    steps:
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
        if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
        with:
          publish_results: ${{ github.ref == 'refs/heads/develop' }}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard publish_results must use the repository default branch guard" in violations
    )


def test_supply_chain_check_rejects_scorecard_global_env_when_publishing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard publish workflows do not use workflow-level env."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_scorecard_global_env",
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
      - main
  schedule:
    - cron: '30 1 * * 1'
env:
  GIT_CONFIG_COUNT: "1"
  GIT_CONFIG_KEY_0: init.defaultBranch
  GIT_CONFIG_VALUE_0: develop
jobs:
  analysis:
    name: ossf-scorecard
    steps:
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
        if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
        with:
          publish_results: PUBLISH_GUARD
""".strip().replace("PUBLISH_GUARD", publish_guard),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert any(
        "ossf scorecard publishing workflow must not contain top-level env or defaults" in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_scorecard_global_defaults_when_publishing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard publish workflows do not use workflow-level defaults."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_scorecard_global_defaults",
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        """
name: ossf-scorecard
on:
  push:
    branches:
      - develop
      - main
  schedule:
    - cron: '30 1 * * 1'
defaults:
  run:
    shell: bash
jobs:
  analysis:
    name: ossf-scorecard
    steps:
      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3
        if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
        with:
          publish_results: PUBLISH_GUARD
""".strip().replace("PUBLISH_GUARD", publish_guard),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert any(
        "ossf scorecard publishing workflow must not contain top-level env or defaults" in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_ossf_publish_job_run_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard publishing jobs satisfy OSSF uses-only restrictions."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_uses_only"
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    scorecard_action = (
        "      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
                scorecard_action,
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Skip OSSF Scorecard on non-default branch",
                f"        if: github.ref != {default_branch_ref}",
                '        run: echo "skip"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert any(
        "ossf scorecard publishing job must only contain uses steps; split run steps "
        "into a separate non-publishing job" in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_ossf_publish_run_steps_in_any_workflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure OSSF publishing restrictions follow Scorecard if it moves workflows."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_any_workflow"
    )
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    scorecard_action = (
        "      - uses: ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
                scorecard_action,
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
            ]
        ),
        encoding="utf-8",
    )
    (workflow_dir / "scorecard-security-gate.yml").write_text(
        "\n".join(
            [
                "name: scorecard-security-gate",
                "on: push",
                "jobs:",
                "  moved-scorecard:",
                "    steps:",
                scorecard_action,
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: extra diagnostics",
                '        run: echo "this breaks OSSF publishing"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert any(
        violation.startswith(".github/workflows/scorecard-security-gate.yml:")
        and "ossf scorecard publishing job must only contain uses steps" in violation
        for violation in violations
    )


def test_supply_chain_check_accepts_repo_ossf_publish_restrictions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in OSSF Scorecard workflow follows publish restrictions."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("ossf scorecard" in violation for violation in violations)


def test_central_governance_workflows_are_consolidated_push_backstops() -> None:
    """Ensure central PR governance leaves one local push security backstop."""
    repo_root = Path(__file__).resolve().parents[3]
    workflows_dir = repo_root / ".github" / "workflows"

    assert not (workflows_dir / "dependency-review.yml").exists()

    security_backstop = workflows_dir / "security-audit.yml"
    assert security_backstop.exists()
    workflow = security_backstop.read_text(encoding="utf-8")
    assert "pull_request:" not in workflow
    for retired_workflow in ("bandit.yml", "codeql.yml", "secret-scan-gate.yml", "trivy.yml"):
        assert not (workflows_dir / retired_workflow).exists()

    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_central"
    )
    required = {path.as_posix() for path in supply_chain.REQUIRED_FILES}
    assert ".github/workflows/dependency-review.yml" not in required
    assert ".github/workflows/codeql.yml" not in required
    assert ".github/workflows/security-audit.yml" in required
    assert ".github/workflows/ossf-scorecard.yml" in required


def test_workflow_concurrency_cancels_only_superseded_pr_heads() -> None:
    """Cancel same-PR stale heads without cancelling push, release, or schedule work."""
    repo_root = Path(__file__).resolve().parents[3]
    workflows_dir = repo_root / ".github" / "workflows"

    for workflow_name in ("build-baseline.yml", "ci.yml", "sbom.yml"):
        workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
        assert "concurrency:" in workflow, workflow_name
        assert "github.workflow }}-${{ github.repository }}" in workflow, workflow_name
        assert "github.event.pull_request.number" in workflow, workflow_name
        assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow

    for workflow_name in ("ossf-scorecard.yml", "release.yml", "security-audit.yml"):
        workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
        assert "concurrency:" in workflow, workflow_name
        assert "cancel-in-progress: false" in workflow, workflow_name
        assert "contents: read" in workflow or "permissions: read-all" in workflow, workflow_name

    assert "pull_request:" not in (workflows_dir / "release.yml").read_text(encoding="utf-8")


def test_opencode_review_declares_top_level_token_permissions() -> None:
    """Ensure OpenCode token posture is delegated to the central required workflow."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "ContextualWisdomLab/.github" in policy
    assert "opencode-review" in policy
    assert "repo-local copies" in policy
    assert "token permissions" in policy


def test_supply_chain_check_rejects_unnormalized_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard SARIF is normalized before CodeQL upload ingestion."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_ossf_sarif_guard"
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_upload_step_with_unnormalized_scorecard_sarif(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure unrelated normalizer tokens cannot bless a raw Scorecard upload step."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_step_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Mention normalizer without protecting upload",
                "        env:",
                "          UNUSED_SARIF_HINT: 'sarif_file: normalized-scorecard-results.sarif'",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py raw.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_scorecard_normalizer_after_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard SARIF normalization must precede upload-sarif."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_order_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  analysis:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
                "      - name: Normalize after upload",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_env_spoofed_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure upload step env cannot spoof the required normalized sarif_file value."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_env_spoof_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        env:",
                "          UNUSED_SARIF_HINT: 'sarif_file: normalized-scorecard-results.sarif'",
                "        with:",
                "          sarif_file: results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_env_spoofed_scorecard_normalizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure env-only normalizer mentions do not satisfy Scorecard normalization."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_normalizer_env_spoof_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  analysis:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        env:",
                "          NORMALIZER_HINT: scripts/checks/normalize_scorecard_sarif.py",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_with_before_uses_raw_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure step field order cannot hide raw Scorecard SARIF uploads."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_step_order_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  analysis:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - with:",
                "          sarif_file: results.sarif",
                "        uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_inline_comment_raw_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure sarif_file inline comments cannot hide raw Scorecard uploads."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_inline_comment_guard",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  analysis:",
                "    name: ossf-scorecard",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with: # upload arguments",
                "          sarif_file: results.sarif # raw Scorecard upload",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_accepts_colocated_non_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure non-Scorecard SARIF uploads are not forced through Scorecard normalization."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_mixed_uploads",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "combined-security.yml").write_text(
        "\n".join(
            [
                "name: combined-security",
                "on: push",
                "jobs:",
                "  scorecard:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
                "  trivy: # scanner SARIF upload",
                "    steps:",
                "      # not ossf/scorecard-action",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: trivy-results.sarif",
            ]
        ),
        encoding="utf-8",
    )
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  placeholder:",
                "    steps:",
                "      - run: echo placeholder",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("ossf scorecard SARIF upload" in violation for violation in violations)


def test_supply_chain_check_accepts_colocated_generic_non_scorecard_sarif_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure separate non-Scorecard jobs may upload generic SARIF filenames."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_generic_mixed_uploads",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "combined-security.yml").write_text(
        "\n".join(
            [
                "name: combined-security",
                "on: push",
                "jobs:",
                "  scorecard:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
                "  trivy: # scanner SARIF upload",
                "    steps:",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: results.sarif",
            ]
        ),
        encoding="utf-8",
    )
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  placeholder:",
                "    steps:",
                "      - run: echo placeholder",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("ossf scorecard SARIF upload" in violation for violation in violations)


def test_supply_chain_check_rejects_mismatched_scorecard_normalizer_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure upload-sarif only accepts the same normalized file the job produced."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_mismatched_normalizer_output",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  scorecard:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "      - uses: "
                "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "          path: results.sarif",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      - uses: "
                "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "          path: scorecard-sarif",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          normalized-scorecard-results.sarif",
                "          other-normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_shell_spoofed_normalizer_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure shell tokens after the normalizer target cannot spoof output matching."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_shell_spoofed_output",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  scorecard:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      - uses: "
                "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          other-normalized-scorecard-results.sarif",
                "          && cp other-normalized-scorecard-results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_rejects_echo_spoofed_normalizer_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure mentioning the normalizer in a non-executing command is rejected."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_sarif_echo_spoofed_normalizer",
    )
    default_branch_ref = "format('refs/heads/{0}', github.event.repository.default_branch)"
    publish_guard = supply_chain.OSSF_DEFAULT_BRANCH_PUBLISH_GUARD.partition(": ")[2]

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on:",
                "  push:",
                "    branches:",
                "      - develop",
                "      - main",
                "  schedule:",
                "    - cron: '30 1 * * 1'",
                "jobs:",
                "  scorecard:",
                "    steps:",
                "      - uses: "
                "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3",
                f"        if: github.ref == {default_branch_ref}",
                "        with:",
                f"          publish_results: {publish_guard}",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      - uses: "
                "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "      - name: Mention normalizer without running it",
                "        run: >-",
                "          echo python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: "
                "github/codeql-action/upload-sarif@95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard SARIF upload must normalize repository-level placeholder URIs "
        "before upload-sarif"
    ) in violations


def test_supply_chain_check_requires_scorecard_download_without_action_decompression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure Scorecard downloads avoid action-owned legacy decompression paths."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_download_decompression_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      - uses: ",
                "          actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "          path: scorecard-sarif",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: ",
                "          github/codeql-action/upload-sarif@"
                "95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard artifact download must use skip-decompress: true and "
        "repo-owned extraction before normalization"
    ) in violations


def test_supply_chain_check_rejects_commented_scorecard_decompression_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure comments cannot spoof the Scorecard artifact extraction guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_download_comment_spoof_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      # skip-decompress: true",
                "      # python3 scripts/checks/extract_scorecard_artifact.py",
                "      # scorecard-artifact scorecard-sarif",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "          path: scorecard-sarif",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: github/codeql-action/upload-sarif@"
                "95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard artifact download must use skip-decompress: true and "
        "repo-owned extraction before normalization"
    ) in violations


def test_supply_chain_check_rejects_echo_spoofed_scorecard_extractor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure echoing the extractor command cannot satisfy artifact extraction."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_download_echo_spoof_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ossf-scorecard.yml").write_text(
        "\n".join(
            [
                "name: ossf-scorecard",
                "on: push",
                "jobs:",
                "  scorecard-sarif-upload:",
                "    steps:",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          name: ossf-scorecard-results",
                "          path: scorecard-artifact",
                "          skip-decompress: true",
                "      - name: Mention extractor without running it",
                "        run: >-",
                "          echo python3 scripts/checks/extract_scorecard_artifact.py",
                "          scorecard-artifact",
                "          scorecard-sarif",
                "      - name: Normalize repository-level Scorecard SARIF locations",
                "        run: >-",
                "          python3 scripts/checks/normalize_scorecard_sarif.py",
                "          scorecard-sarif/results.sarif",
                "          normalized-scorecard-results.sarif",
                "      - uses: github/codeql-action/upload-sarif@"
                "95e58e9a2cdfd71adc6e0353d5c52f41a045d225",
                "        with:",
                "          sarif_file: normalized-scorecard-results.sarif",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "ossf scorecard artifact download must use skip-decompress: true and "
        "repo-owned extraction before normalization"
    ) in violations


def test_supply_chain_check_accepts_repo_scorecard_download_decompression_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in Scorecard downloads use repo-owned artifact extraction."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_ossf_download_decompression_repo",
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("skip-decompress" in violation for violation in violations)


def test_supply_chain_check_rejects_release_artifact_download_action_decompression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure release artifact downloads avoid action-owned ZIP decompression."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_download_decompression_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        "\n".join(
            [
                "name: build-baseline",
                "on:",
                "  push:",
                "    branches: [develop, main]",
                "    tags: ['v*']",
                "jobs:",
                "  publish-immutable-release:",
                "    name: release-artifact / publish",
                "    steps:",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          pattern: bandscope-*-${{ github.sha }}",
                "          path: artifacts",
                "          merge-multiple: true",
                "      - name: Validate release asset set",
                "        run: >-",
                "          python3 scripts/release/select_release_assets.py",
                "          --output release-assets.txt",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "release artifact download must use skip-decompress: true and "
        "repo-owned extraction before asset validation"
    ) in violations


def test_supply_chain_check_accepts_repo_release_artifact_download_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in release downloads use repo-owned artifact extraction."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_download_decompression_repo",
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_workflow_coverage()

    assert not any("release artifact download must use" in violation for violation in violations)


@pytest.mark.parametrize(
    "spoof_line",
    [
        "        if: ${{ false }}",
        "        continue-on-error: true",
        '        continue-on-error: "true"',
        "        continue-on-error: ${{ true }}",
    ],
)
def test_supply_chain_check_rejects_non_blocking_release_extractor_spoofs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spoof_line: str
) -> None:
    """Ensure skipped or non-blocking extractor steps cannot satisfy the guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_download_non_blocking_spoof_guard",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        "\n".join(
            [
                "name: build-baseline",
                "jobs:",
                "  publish-immutable-release:",
                "    name: release-artifact / publish",
                "    steps:",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          pattern: bandscope-*-${{ github.sha }}",
                "          path: downloaded-artifacts",
                "          skip-decompress: true",
                "      - name: Spoof release artifact extraction",
                spoof_line,
                "        run: >-",
                "          python3 scripts/release/extract_release_artifacts.py",
                "          downloaded-artifacts",
                "          artifacts",
                "      - name: Validate release asset set",
                "        run: >-",
                "          python3 scripts/release/select_release_assets.py",
                "          --output release-assets.txt",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "release artifact download must use skip-decompress: true and "
        "repo-owned extraction before asset validation"
    ) in violations


def test_supply_chain_check_accepts_false_continue_on_error_release_extractor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure explicitly blocking release extraction still satisfies the guard."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_false_continue_on_error_release_extractor",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        "\n".join(
            [
                "name: build-baseline",
                "jobs:",
                "  publish-immutable-release:",
                "    name: release-artifact / publish",
                "    steps:",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          pattern: bandscope-*-${{ github.sha }}",
                "          path: downloaded-artifacts",
                "          skip-decompress: true",
                "      - name: Extract release artifacts with repo-owned validation",
                "        continue-on-error: false",
                "        run: >-",
                "          python3 scripts/release/extract_release_artifacts.py",
                "          downloaded-artifacts",
                "          artifacts",
                "      - name: Validate release asset set",
                "        run: >-",
                "          python3 scripts/release/select_release_assets.py",
                "          --output release-assets.txt",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert not any(
        "release artifact download must use skip-decompress: true" in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_release_download_env_skip_decompress_spoof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure skip-decompress must be scoped under download-artifact with."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_download_env_skip_decompress_spoof",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        "\n".join(
            [
                "name: build-baseline",
                "jobs:",
                "  publish-immutable-release:",
                "    name: release-artifact / publish",
                "    steps:",
                "      - uses: actions/download-artifact@"
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
                "        with:",
                "          pattern: bandscope-*-${{ github.sha }}",
                "          path: downloaded-artifacts",
                "        env:",
                "          skip-decompress: true",
                "      - name: Extract release artifacts with repo-owned validation",
                "        run: >-",
                "          python3 scripts/release/extract_release_artifacts.py",
                "          downloaded-artifacts",
                "          artifacts",
                "      - name: Validate release asset set",
                "        run: >-",
                "          python3 scripts/release/select_release_assets.py",
                "          --output release-assets.txt",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_coverage()

    assert (
        "release artifact download must use skip-decompress: true and "
        "repo-owned extraction before asset validation"
    ) in violations


def test_release_artifact_extractor_restores_expected_release_files(
    tmp_path: Path,
) -> None:
    """Ensure release artifact ZIPs extract only allowlisted artifact files."""
    extractor = load_module(
        "scripts/release/extract_release_artifacts.py", "extract_release_artifacts"
    )
    artifact_dir = tmp_path / "downloaded-artifacts"
    artifact_dir.mkdir()
    output_dir = tmp_path / "artifacts"
    source_zip = artifact_dir / "bandscope-windows-amd64.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe", "installer")
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe.sha256", "digest")
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe.manifest.txt", "manifest")

    extracted = extractor.extract_release_artifacts(artifact_dir, output_dir)

    assert extracted == [
        output_dir / "bandscope-windows-amd64-abcdef123456.exe",
        output_dir / "bandscope-windows-amd64-abcdef123456.exe.manifest.txt",
        output_dir / "bandscope-windows-amd64-abcdef123456.exe.sha256",
    ]
    assert (output_dir / "bandscope-windows-amd64-abcdef123456.exe").read_text(
        encoding="utf-8"
    ) == "installer"


def test_release_artifact_extractor_rejects_unsafe_members(tmp_path: Path) -> None:
    """Ensure release artifact extraction rejects paths outside the allowlist."""
    extractor = load_module(
        "scripts/release/extract_release_artifacts.py",
        "extract_release_artifacts_rejects_unsafe_members",
    )
    artifact_dir = tmp_path / "downloaded-artifacts"
    artifact_dir.mkdir()
    with zipfile.ZipFile(artifact_dir / "poison.zip", "w") as archive:
        archive.writestr("../poison.sh", "owned")

    with pytest.raises(ValueError, match="unexpected release artifact member"):
        extractor.extract_release_artifacts(artifact_dir, tmp_path / "artifacts")


def test_release_artifact_extractor_rejects_oversized_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure oversized release artifacts fail closed and remove partial files."""
    extractor = load_module(
        "scripts/release/extract_release_artifacts.py",
        "extract_release_artifacts_rejects_oversized_members",
    )
    monkeypatch.setattr(extractor, "MAX_RELEASE_ARTIFACT_BYTES", 4)
    artifact_dir = tmp_path / "downloaded-artifacts"
    artifact_dir.mkdir()
    with zipfile.ZipFile(artifact_dir / "bandscope-windows-amd64.zip", "w") as archive:
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe", "installer")
    output_dir = tmp_path / "artifacts"

    with pytest.raises(ValueError, match="release artifact member too large"):
        extractor.extract_release_artifacts(artifact_dir, output_dir)

    assert not (output_dir / "bandscope-windows-amd64-abcdef123456.exe").exists()


def test_release_artifact_extractor_rejects_oversized_total_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure many small release artifact members cannot exceed the total cap."""
    extractor = load_module(
        "scripts/release/extract_release_artifacts.py",
        "extract_release_artifacts_rejects_oversized_total",
    )
    monkeypatch.setattr(extractor, "MAX_RELEASE_ARTIFACT_BYTES", 8)
    monkeypatch.setattr(extractor, "MAX_TOTAL_RELEASE_ARTIFACT_BYTES", 8)
    artifact_dir = tmp_path / "downloaded-artifacts"
    artifact_dir.mkdir()
    with zipfile.ZipFile(artifact_dir / "bandscope-windows-amd64.zip", "w") as archive:
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe", "1234")
        archive.writestr("bandscope-windows-amd64-fedcba654321.exe", "56789")

    with pytest.raises(ValueError, match="release artifact bundle too large"):
        extractor.extract_release_artifacts(artifact_dir, tmp_path / "artifacts")


def test_release_artifact_extractor_rejects_too_many_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure artifact ZIPs cannot contain unbounded allowlist-shaped files."""
    extractor = load_module(
        "scripts/release/extract_release_artifacts.py",
        "extract_release_artifacts_rejects_too_many_members",
    )
    monkeypatch.setattr(extractor, "MAX_RELEASE_ARTIFACT_FILES", 1)
    artifact_dir = tmp_path / "downloaded-artifacts"
    artifact_dir.mkdir()
    with zipfile.ZipFile(artifact_dir / "bandscope-windows-amd64.zip", "w") as archive:
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe", "installer")
        archive.writestr("bandscope-windows-amd64-abcdef123456.exe.sha256", "digest")

    with pytest.raises(ValueError, match="too many release artifact files"):
        extractor.extract_release_artifacts(artifact_dir, tmp_path / "artifacts")


def test_scorecard_artifact_extractor_extracts_expected_sarif(tmp_path: Path) -> None:
    """Ensure the repo-owned extractor restores results.sarif from zipped artifacts."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py", "extract_scorecard_artifact"
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    output_dir = tmp_path / "scorecard-sarif"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("results.sarif", '{"version":"2.1.0","runs":[]}')

    extracted = extractor.extract_scorecard_artifact(source_zip, output_dir)

    assert extracted == output_dir / "results.sarif"
    assert extracted.read_text(encoding="utf-8") == '{"version":"2.1.0","runs":[]}'

    artifact_dir = tmp_path / "scorecard-artifact"
    artifact_dir.mkdir()
    directory_source_zip = artifact_dir / "results.sarif.zip"
    with zipfile.ZipFile(directory_source_zip, "w") as archive:
        archive.writestr("results.sarif", '{"version":"2.1.0","runs":[{}]}')

    directory_output_dir = tmp_path / "directory-scorecard-sarif"
    directory_extracted = extractor.extract_scorecard_artifact(artifact_dir, directory_output_dir)

    assert directory_extracted == directory_output_dir / "results.sarif"
    assert directory_extracted.read_text(encoding="utf-8") == '{"version":"2.1.0","runs":[{}]}'

    empty_artifact_dir = tmp_path / "empty-scorecard-artifact"
    empty_artifact_dir.mkdir()
    with pytest.raises(ValueError, match="expected exactly one Scorecard artifact zip"):
        extractor.extract_scorecard_artifact(empty_artifact_dir, tmp_path / "empty-output")

    multi_artifact_dir = tmp_path / "multi-scorecard-artifact"
    multi_artifact_dir.mkdir()
    with zipfile.ZipFile(multi_artifact_dir / "first.zip", "w") as archive:
        archive.writestr("results.sarif", "{}")
    with zipfile.ZipFile(multi_artifact_dir / "second.zip", "w") as archive:
        archive.writestr("results.sarif", "{}")
    with pytest.raises(ValueError, match="expected exactly one Scorecard artifact zip"):
        extractor.extract_scorecard_artifact(multi_artifact_dir, tmp_path / "multi-output")


def test_scorecard_artifact_extractor_rejects_symlink_artifact_zip(
    tmp_path: Path,
) -> None:
    """Ensure input artifact paths are not followed through symlinks."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_input_symlink",
    )
    real_zip = tmp_path / "real-scorecard-results.zip"
    with zipfile.ZipFile(real_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")
    symlink_zip = tmp_path / "ossf-scorecard-results.zip"
    make_symlink_or_skip(symlink_zip, real_zip)

    with pytest.raises(ValueError, match="symlinked artifact path"):
        extractor.extract_scorecard_artifact(symlink_zip, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_symlink_zip_in_artifact_directory(
    tmp_path: Path,
) -> None:
    """Ensure directory inputs reject symlinked ZIP candidates and fail closed."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_directory_symlink",
    )
    artifact_dir = tmp_path / "scorecard-artifact"
    artifact_dir.mkdir()
    real_zip = tmp_path / "real-scorecard-results.zip"
    with zipfile.ZipFile(real_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")
    make_symlink_or_skip(artifact_dir / "results.sarif.zip", real_zip)

    with pytest.raises(ValueError, match="symlinked artifact path"):
        extractor.extract_scorecard_artifact(artifact_dir, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_mixed_symlink_zip_directory(
    tmp_path: Path,
) -> None:
    """Ensure any symlinked ZIP candidate taints directory artifact input."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_mixed_directory_symlink",
    )
    artifact_dir = tmp_path / "scorecard-artifact"
    artifact_dir.mkdir()
    with zipfile.ZipFile(artifact_dir / "results.sarif.zip", "w") as archive:
        archive.writestr("results.sarif", "{}")
    real_zip = tmp_path / "real-scorecard-results.zip"
    with zipfile.ZipFile(real_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")
    make_symlink_or_skip(artifact_dir / "shadow.zip", real_zip)

    with pytest.raises(ValueError, match="symlinked artifact path"):
        extractor.extract_scorecard_artifact(artifact_dir, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_path_traversal(tmp_path: Path) -> None:
    """Ensure malformed Scorecard artifacts cannot escape the extraction directory."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_traversal",
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("../results.sarif", "{}")

    with pytest.raises(ValueError, match="unexpected artifact member"):
        extractor.extract_scorecard_artifact(source_zip, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_zip_symlink(tmp_path: Path) -> None:
    """Ensure symlink-like ZIP members are rejected even with the expected name."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_symlink",
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    symlink_info = zipfile.ZipInfo("results.sarif")
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr(symlink_info, "target")

    with pytest.raises(ValueError, match="unexpected artifact member"):
        extractor.extract_scorecard_artifact(source_zip, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_missing_results_sarif(
    tmp_path: Path,
) -> None:
    """Ensure artifacts without the expected Scorecard SARIF fail closed."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_missing",
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.comment = b"empty artifact fixture"

    with pytest.raises(ValueError, match="expected only results.sarif"):
        extractor.extract_scorecard_artifact(source_zip, tmp_path / "scorecard-sarif")


def test_scorecard_artifact_extractor_rejects_symlink_output_dir(tmp_path: Path) -> None:
    """Ensure output directories are not followed through symlinks."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_output_dir_symlink",
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    symlink_output = tmp_path / "scorecard-sarif"
    make_symlink_or_skip(symlink_output, real_output, target_is_directory=True)
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")

    with pytest.raises(ValueError, match="symlinked output path"):
        extractor.extract_scorecard_artifact(source_zip, symlink_output)


def test_scorecard_artifact_extractor_rejects_existing_target_symlink(
    tmp_path: Path,
) -> None:
    """Ensure existing target symlinks cannot be overwritten by extraction."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_target_symlink",
    )
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    output_dir = tmp_path / "scorecard-sarif"
    output_dir.mkdir()
    outside_target = tmp_path / "outside.sarif"
    outside_target.write_text("outside", encoding="utf-8")
    make_symlink_or_skip(output_dir / "results.sarif", outside_target)
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")

    with pytest.raises(FileExistsError):
        extractor.extract_scorecard_artifact(source_zip, output_dir)
    assert outside_target.read_text(encoding="utf-8") == "outside"


def test_scorecard_artifact_extractor_rejects_oversized_results_sarif(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure oversized Scorecard SARIF artifacts fail before extraction."""
    extractor = load_module(
        "scripts/checks/extract_scorecard_artifact.py",
        "extract_scorecard_artifact_oversized",
    )
    monkeypatch.setattr(extractor, "MAX_SARIF_BYTES", 1)
    source_zip = tmp_path / "ossf-scorecard-results.zip"
    output_dir = tmp_path / "scorecard-sarif"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("results.sarif", "{}")

    with pytest.raises(ValueError, match="artifact member too large"):
        extractor.extract_scorecard_artifact(source_zip, output_dir)

    assert not (output_dir / "results.sarif").exists()


def test_scorecard_sarif_normalizer_replaces_repository_level_placeholder(
    tmp_path: Path,
) -> None:
    """Ensure repository-level Scorecard SARIF locations use upload-safe URIs."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py", "normalize_scorecard_sarif"
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "Token-Permissions",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            }
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    location = normalized["runs"][0]["results"][0]["locations"][0]["physicalLocation"]

    assert rewritten == 1
    assert location["artifactLocation"]["uri"] == ".github/workflows/ossf-scorecard.yml"
    assert location["region"]["startLine"] == 1
    assert location["properties"]["bandscopeOriginalUri"] == ("no file associated with this alert")
    assert location["properties"]["bandscopeRepositoryLevelFinding"] is True


def test_scorecard_sarif_normalizer_preserves_file_locations(tmp_path: Path) -> None:
    """Ensure file-associated Scorecard SARIF locations are not rewritten."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py", "normalize_scorecard_sarif_preserve"
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "Pinned-Dependencies",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": ".github/workflows/ci.yml"},
                                            "region": {"startLine": 12},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    location = normalized["runs"][0]["results"][0]["locations"][0]["physicalLocation"]

    assert rewritten == 0
    assert location["artifactLocation"]["uri"] == ".github/workflows/ci.yml"
    assert location["region"]["startLine"] == 12
    assert "properties" not in location


def test_scorecard_sarif_normalizer_downgrades_non_blocking_cii_badge_result(
    tmp_path: Path,
) -> None:
    """Ensure the badge signal keeps Scorecard analysis without blocking gates."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py",
        "normalize_scorecard_sarif_cii_badge",
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "CIIBestPracticesID",
                                "message": {
                                    "text": (
                                        "no effort to earn an OpenSSF best practices badge detected"
                                    )
                                },
                            },
                            {
                                "ruleId": "TokenPermissionsID",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            }
                                        }
                                    }
                                ],
                            },
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    results = normalized["runs"][0]["results"]
    cii_result = results[0]
    cii_location = cii_result["locations"][0]["physicalLocation"]

    assert rewritten == 5
    assert [result["ruleId"] for result in results] == [
        "CIIBestPracticesID",
        "TokenPermissionsID",
    ]
    assert cii_result["level"] == "note"
    assert cii_result["properties"]["bandscopeNonBlockingScorecardSignal"] is True
    assert cii_location["artifactLocation"]["uri"] == ".github/workflows/ossf-scorecard.yml"
    assert cii_location["region"]["startLine"] == 1


def test_scorecard_sarif_normalizer_fills_existing_region_start_line(
    tmp_path: Path,
) -> None:
    """Ensure repository-level SARIF locations with a region still get startLine."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py", "normalize_scorecard_sarif_region"
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "Token-Permissions",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "region": {},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    physical_location = normalized["runs"][0]["results"][0]["locations"][0]["physicalLocation"]

    assert rewritten == 1
    assert physical_location["region"]["startLine"] == 1


def test_scorecard_sarif_normalizer_repairs_invalid_region_start_lines(
    tmp_path: Path,
) -> None:
    """Ensure invalid repository-level SARIF region startLine values become valid."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py",
        "normalize_scorecard_sarif_invalid_region",
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "Token-Permissions",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "region": {"startLine": 0},
                                        }
                                    },
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "region": {"startLine": None},
                                        }
                                    },
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "region": {"startLine": "7"},
                                        }
                                    },
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "region": {"startLine": 3},
                                        }
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    locations = normalized["runs"][0]["results"][0]["locations"]

    assert rewritten == 4
    assert [location["physicalLocation"]["region"]["startLine"] for location in locations] == [
        1,
        1,
        1,
        3,
    ]


def test_scorecard_sarif_normalizer_skips_malformed_locations(tmp_path: Path) -> None:
    """Ensure malformed Scorecard SARIF arrays do not crash normalization."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py", "normalize_scorecard_sarif_malformed"
    )
    source = tmp_path / "results.sarif"
    target = tmp_path / "normalized-results.sarif"
    source.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    "not-a-run",
                    {
                        "results": [
                            "not-a-result",
                            {
                                "ruleId": "Token-Permissions",
                                "locations": [
                                    "not-a-location",
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "no file associated with this alert"
                                            },
                                            "properties": "not-properties",
                                        }
                                    },
                                ],
                            },
                        ]
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rewritten = normalizer.normalize_scorecard_sarif(source, target)
    normalized = json.loads(target.read_text(encoding="utf-8"))
    physical_location = normalized["runs"][1]["results"][1]["locations"][1]["physicalLocation"]

    assert rewritten == 1
    assert physical_location["artifactLocation"]["uri"] == (".github/workflows/ossf-scorecard.yml")
    assert physical_location["properties"]["bandscopeRepositoryLevelFinding"] is True


def test_scorecard_sarif_normalizer_skips_malformed_containers(
    tmp_path: Path,
) -> None:
    """Ensure non-list SARIF containers do not crash normalization."""
    normalizer = load_module(
        "scripts/checks/normalize_scorecard_sarif.py",
        "normalize_scorecard_sarif_malformed_containers",
    )
    cases = [
        {"version": "2.1.0", "runs": None},
        {"version": "2.1.0", "runs": {"results": []}},
        {"version": "2.1.0", "runs": [{"results": None}]},
        {"version": "2.1.0", "runs": [{"results": {"locations": []}}]},
        {"version": "2.1.0", "runs": [{"results": [{"locations": None}]}]},
        {"version": "2.1.0", "runs": [{"results": [{"locations": {}}]}]},
    ]

    for index, sarif in enumerate(cases):
        source = tmp_path / f"results-{index}.sarif"
        target = tmp_path / f"normalized-results-{index}.sarif"
        source.write_text(json.dumps(sarif), encoding="utf-8")

        rewritten = normalizer.normalize_scorecard_sarif(source, target)

        assert rewritten == 0
        assert json.loads(target.read_text(encoding="utf-8")) == sarif


def test_supply_chain_check_rejects_vulnerable_rust_rand_lockfile(
    tmp_path: Path,
) -> None:
    """Ensure the Rust lockfile cannot regress to vulnerable rand ranges."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_rand_vulnerable"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "rand"
version = "0.8.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "placeholder"

[[package]]
name = "rand"
version = "0.9.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "newer-vulnerable-api-series"

[[package]]
name = "rand"
version = "0.10.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "latest-vulnerable-api-series"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (f"{lockfile}: rand 0.8.5 is below patched 0.8.6 for GHSA-cq8v-f236-94qc") in violations
    assert (f"{lockfile}: rand 0.9.2 is below patched 0.9.3 for GHSA-cq8v-f236-94qc") in violations
    assert (
        f"{lockfile}: rand 0.10.0 is below patched 0.10.1 for GHSA-cq8v-f236-94qc"
    ) in violations


def test_supply_chain_check_rejects_non_exception_rust_rand_0_7_lockfile(
    tmp_path: Path,
) -> None:
    """Ensure legacy rand 0.7.x entries cannot be reintroduced."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_rand_0_7"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
version = "0.7.4"
name = "rand"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "unexpected-legacy-series"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.4 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_check_handles_version_first_and_inline_dependency_fixtures(
    tmp_path: Path,
) -> None:
    """Ensure valid Cargo.lock key order and inline dependencies stay guarded."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_rand_format_variants",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
version = "1.0.0"
name = "bad-owner"
dependencies = ["rand 0.7.3"]

[[package]]
version = "0.7.3"
name = "rand"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "version-first-inline-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.3 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_cargo_lock_parser_uses_toml_values() -> None:
    """Ensure Cargo.lock inline values are parsed as TOML."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_cargo_parser_toml_values",
    )

    assert supply_chain.parse_cargo_lock_string_list('["rand 0.7.3", "serde"]') == [
        "rand 0.7.3",
        "serde",
    ]
    assert supply_chain.parse_cargo_lock_string_list('"not-list"') == []
    assert supply_chain.parse_cargo_lock_scalar('"rand"') == "rand"
    assert supply_chain.parse_cargo_lock_scalar('"0.8.6"') == "0.8.6"


def test_supply_chain_cargo_lock_parser_rejects_non_toml_values() -> None:
    """Ensure malformed Cargo.lock values fail closed instead of evaluating code."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_cargo_parser_invalid_values",
    )

    assert (
        supply_chain.parse_cargo_lock_string_list('["rand", __import__("os").system("echo pwn")]')
        == []
    )
    assert supply_chain.parse_cargo_lock_scalar("{not valid") == ""


def test_supply_chain_check_reports_missing_rust_lockfile(tmp_path: Path) -> None:
    """Ensure missing Cargo.lock is reported as a supply-chain violation."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_lock_missing"
    )
    lockfile = tmp_path / "missing" / "Cargo.lock"

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert f"Cargo.lock missing: {lockfile}" in violations


def test_supply_chain_check_rejects_unowned_legacy_rust_rand_exception(
    tmp_path: Path,
) -> None:
    """Ensure rand 0.7.3 is rejected after retiring the owner-chain exception."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_rand_unowned"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
version = "0.7.3"
name = "rand"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "wrong-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.3 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_check_rejects_inline_dependency_legacy_rust_rand_owner(
    tmp_path: Path,
) -> None:
    """Ensure inline dependency arrays cannot hide retired rand owners."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_rand_inline_owner",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri-utils"
version = "2.8.3"
dependencies = ["kuchikiki 0.8.8-speedreader"]

[[package]]
name = "kuchikiki"
version = "0.8.8-speedreader"
dependencies = ["selectors 0.24.0"]

[[package]]
name = "selectors"
version = "0.24.0"
dependencies = ["phf_codegen 0.8.0"]

[[package]]
name = "phf_codegen"
version = "0.8.0"
dependencies = ["phf_generator 0.8.0"]

[[package]]
name = "phf_generator"
version = "0.8.0"
dependencies = ["rand 0.7.3"]

[[package]]
name = "bad-owner"
version = "1.0.0"
dependencies = ["rand 0.7.3"]

[[package]]
name = "rand"
version = "0.7.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "legacy-exception"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.3 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_check_rejects_documented_legacy_rust_rand_owner_chain(
    tmp_path: Path,
) -> None:
    """Ensure the former rand 0.7.3 exception cannot be reintroduced."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_rand_retired_owner",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri-utils"
version = "2.8.3"
dependencies = ["kuchikiki 0.8.8-speedreader"]

[[package]]
name = "kuchikiki"
version = "0.8.8-speedreader"
dependencies = ["selectors 0.24.0"]

[[package]]
name = "selectors"
version = "0.24.0"
dependencies = ["phf_codegen 0.8.0"]

[[package]]
name = "phf_codegen"
version = "0.8.0"
dependencies = ["phf_generator 0.8.0"]

[[package]]
name = "phf_generator"
version = "0.8.0"
dependencies = ["rand 0.7.3"]

[[package]]
name = "rand"
version = "0.7.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "retired-exception"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.3 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_check_reports_non_numeric_rust_rand_versions(
    tmp_path: Path,
) -> None:
    """Ensure non-standard rand versions are reported instead of crashing."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_rand_non_numeric_version",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "rand"
version = "0.9.3-alpha.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "non-stable"

[[package]]
name = "rand"
version = "0.8.6.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "extra-numeric-segment"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.9.3-alpha.1 has a non-numeric version segment for GHSA-cq8v-f236-94qc"
    ) in violations
    assert (
        f"{lockfile}: rand 0.8.6.1 has a non-standard extra version segment for GHSA-cq8v-f236-94qc"
    ) in violations


def test_supply_chain_check_rejects_mixed_owner_legacy_rust_rand_exception(
    tmp_path: Path,
) -> None:
    """Ensure the retired legacy chain does not exempt rand owners."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_rand_mixed_owner"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri-utils"
version = "2.8.3"
dependencies = [
 "kuchikiki 0.8.8-speedreader",
]

[[package]]
name = "kuchikiki"
version = "0.8.8-speedreader"
dependencies = [
 "selectors 0.24.0",
]

[[package]]
name = "selectors"
version = "0.24.0"
dependencies = [
 "phf_codegen 0.8.0",
]

[[package]]
name = "phf_codegen"
version = "0.8.0"
dependencies = [
 "phf_generator 0.8.0",
]

[[package]]
name = "phf_generator"
version = "0.8.0"
dependencies = [
 "rand 0.7.3",
]

[[package]]
name = "bad-owner"
version = "1.0.0"
dependencies = [
 "rand 0.7.3",
]

[[package]]
name = "rand"
version = "0.7.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "legacy-exception"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: rand 0.7.3 is not allowed for GHSA-cq8v-f236-94qc; "
        "the former legacy owner-chain exception has been removed"
    ) in violations


def test_supply_chain_check_accepts_repo_rust_rand_patch() -> None:
    """Ensure the checked-in Rust lockfile keeps rand on the patched 0.8 line."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_rand_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    violations = supply_chain.rust_dependency_advisory_violations(
        repo_root / "apps" / "desktop" / "src-tauri" / "Cargo.lock"
    )

    assert not violations


def test_supply_chain_check_rejects_yanked_rust_fastrand_lockfile(
    tmp_path: Path,
) -> None:
    """Ensure the Rust lockfile cannot regress to yanked fastrand 2.4.0."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_fastrand_yanked"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "fastrand"
version = "2.4.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "placeholder"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert f"{lockfile}: fastrand 2.4.0 is yanked and must stay updated" in violations


def test_supply_chain_check_accepts_repo_rust_fastrand_update() -> None:
    """Ensure the checked-in Rust lockfile keeps fastrand off yanked 2.4.0."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_fastrand_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    violations = supply_chain.rust_dependency_advisory_violations(
        repo_root / "apps" / "desktop" / "src-tauri" / "Cargo.lock"
    )

    assert not violations


def test_supply_chain_check_rejects_tracked_rust_rand_legacy_exception() -> None:
    """Ensure the fixed legacy rand advisory no longer has an audit exception."""
    repo_root = Path(__file__).resolve().parents[3]
    audit_config = repo_root / "apps" / "desktop" / "src-tauri" / ".cargo" / "audit.toml"
    content = audit_config.read_text(encoding="utf-8")

    assert "RUSTSEC-2026-0097" not in content


def test_supply_chain_check_rejects_stale_rust_fxhash_exception() -> None:
    """Ensure removed fxhash advisories no longer keep stale audit exceptions."""
    repo_root = Path(__file__).resolve().parents[3]
    audit_config = repo_root / "apps" / "desktop" / "src-tauri" / ".cargo" / "audit.toml"
    lockfile = repo_root / "apps" / "desktop" / "src-tauri" / "Cargo.lock"
    audit_content = audit_config.read_text(encoding="utf-8")
    lock_content = lockfile.read_text(encoding="utf-8")

    assert 'name = "fxhash"' not in lock_content
    assert "RUSTSEC-2025-0057" not in audit_content


def test_supply_chain_check_rejects_unowned_legacy_rust_glib_exception(
    tmp_path: Path,
) -> None:
    """Ensure glib 0.18.5 is exempt only on the documented Tauri GTK stack."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_glib_unowned"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "bad-owner"
version = "1.0.0"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "wrong-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_mixed_owner_legacy_rust_glib_exception(
    tmp_path: Path,
) -> None:
    """Ensure a valid Tauri GTK chain does not exempt unrelated glib owners."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_rust_glib_mixed_owner"
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri"
version = "2.10.3"
dependencies = ["tauri-runtime-wry 2.10.1"]

[[package]]
name = "tauri-runtime-wry"
version = "2.10.1"
dependencies = ["wry 0.54.4"]

[[package]]
name = "wry"
version = "0.54.4"
dependencies = ["webkit2gtk 2.0.2"]

[[package]]
name = "webkit2gtk"
version = "2.0.2"
dependencies = ["gtk 0.18.2"]

[[package]]
name = "gtk"
version = "0.18.2"
dependencies = ["glib 0.18.5"]

[[package]]
name = "bad-owner"
version = "1.0.0"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "mixed-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_shared_intermediate_rust_glib_owner(
    tmp_path: Path,
) -> None:
    """Ensure a non-Tauri root cannot hide behind a shared GTK owner."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_shared_intermediate_owner",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri"
version = "2.11.0"
dependencies = ["tauri-runtime-wry 2.11.0"]

[[package]]
name = "tauri-runtime-wry"
version = "2.11.0"
dependencies = ["wry 0.55.0"]

[[package]]
name = "wry"
version = "0.55.0"
dependencies = ["webkit2gtk 2.0.2"]

[[package]]
name = "webkit2gtk"
version = "2.0.2"
dependencies = ["gtk 0.18.2"]

[[package]]
name = "bad-root"
version = "1.0.0"
dependencies = ["gtk 0.18.2"]

[[package]]
name = "gtk"
version = "0.18.2"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "shared-intermediate"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_app_root_direct_rust_glib_path(
    tmp_path: Path,
) -> None:
    """Ensure the app root reaches legacy glib only through the Tauri chain."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_app_root_direct_path",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "bandscope-desktop"
version = "0.1.0"
dependencies = ["tauri 2.11.0", "gtk 0.18.2"]

[[package]]
name = "tauri"
version = "2.11.0"
dependencies = ["tauri-runtime-wry 2.11.0"]

[[package]]
name = "tauri-runtime-wry"
version = "2.11.0"
dependencies = ["wry 0.55.0"]

[[package]]
name = "wry"
version = "0.55.0"
dependencies = ["webkit2gtk 2.0.2"]

[[package]]
name = "webkit2gtk"
version = "2.0.2"
dependencies = ["gtk 0.18.2"]

[[package]]
name = "gtk"
version = "0.18.2"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "app-root-direct-path"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_tauri_direct_rust_glib_owner(
    tmp_path: Path,
) -> None:
    """Ensure Tauri ancestry alone does not allow a direct glib shortcut."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_tauri_direct_owner",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "bandscope-desktop"
version = "0.1.0"
dependencies = ["tauri 2.11.0"]

[[package]]
name = "tauri"
version = "2.11.0"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "tauri-direct-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_short_tauri_rust_glib_path(
    tmp_path: Path,
) -> None:
    """Ensure Tauri-owned glib still needs a complete WebKit/GTK path."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_short_tauri_path",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "bandscope-desktop"
version = "0.1.0"
dependencies = ["tauri 2.11.0"]

[[package]]
name = "tauri"
version = "2.11.0"
dependencies = ["gtk 0.18.2"]

[[package]]
name = "gtk"
version = "0.18.2"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "short-tauri-path"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_tauri_reachable_unexpected_rust_glib_owner(
    tmp_path: Path,
) -> None:
    """Ensure Tauri reachability alone does not broaden the glib exception."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_tauri_bad_owner",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "tauri"
version = "2.11.0"
dependencies = ["bad-owner 1.0.0"]

[[package]]
name = "bad-owner"
version = "1.0.0"
dependencies = ["glib 0.18.5"]

[[package]]
name = "glib"
version = "0.18.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "tauri-reachable-wrong-owner"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.18.5 matches the legacy exception version but "
        "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
        "for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_reports_non_numeric_rust_glib_versions(
    tmp_path: Path,
) -> None:
    """Ensure non-standard glib versions are reported instead of passing closed."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_rust_glib_non_numeric_version",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text(
        """
[[package]]
name = "glib"
version = "0.19.3-alpha.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "non-stable"

[[package]]
name = "glib"
version = "0.18.5.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "extra-numeric-segment"
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_dependency_advisory_violations(lockfile)

    assert (
        f"{lockfile}: glib 0.19.3-alpha.1 has a non-numeric version segment for RUSTSEC-2024-0429"
    ) in violations
    assert (
        f"{lockfile}: glib 0.18.5.1 has a non-standard extra version segment for RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_requires_tracked_rust_glib_legacy_exception() -> None:
    """Ensure the remaining legacy glib advisory is narrowly documented."""
    repo_root = Path(__file__).resolve().parents[3]
    audit_config = repo_root / "apps" / "desktop" / "src-tauri" / ".cargo" / "audit.toml"
    trivy_ignore = repo_root / ".trivyignore"
    content = audit_config.read_text(encoding="utf-8")
    trivy_content = trivy_ignore.read_text(encoding="utf-8")

    assert (
        '"RUSTSEC-2024-0429", # glib 0.18.5: VariantStrIter unsoundness, '
        "transitive via Tauri/wry/webkit2gtk/gtk GTK3 stack; remove when upstream "
        "drops or patches the chain"
    ) in content
    assert "GHSA-wrw7-89jp-8q8g exp:2026-10-31" in trivy_content
    assert "RUSTSEC-2024-0429" in trivy_content
    assert "glib >=0.20" in trivy_content


def test_supply_chain_check_accepts_repo_osv_rust_exceptions() -> None:
    """Ensure OSV Scanner ignores stay aligned with cargo-audit exceptions."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_osv_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    violations = supply_chain.rust_osv_exception_violations(
        repo_root / "apps" / "desktop" / "src-tauri" / ".cargo" / "audit.toml",
        repo_root / "apps" / "desktop" / "src-tauri" / "osv-scanner.toml",
    )

    assert not violations


def test_supply_chain_check_accepts_repo_trivy_rust_exception() -> None:
    """Ensure Trivy carries the same narrow glib exception with a revisit date."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_trivy_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    violations = supply_chain.rust_trivy_exception_violations(
        repo_root / ".trivyignore",
        repo_root / "apps" / "desktop" / "src-tauri" / ".cargo" / "audit.toml",
        repo_root / "apps" / "desktop" / "src-tauri" / "osv-scanner.toml",
    )

    assert not violations


def test_supply_chain_check_rejects_osv_exception_drift(tmp_path: Path) -> None:
    """Ensure OSV exceptions cannot silently diverge from cargo-audit scope."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_osv_drift"
    )
    audit_config = tmp_path / "audit.toml"
    osv_config = tmp_path / "osv-scanner.toml"
    audit_config.write_text(
        """
[advisories]
ignore = ["RUSTSEC-2024-0429"]
""".strip(),
        encoding="utf-8",
    )
    osv_config.write_text(
        """
[[IgnoredVulns]]
id = "RUSTSEC-2024-0413"
reason = ""
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_osv_exception_violations(audit_config, osv_config)

    assert (
        f"{osv_config}: missing OSV ignore for RUSTSEC-2024-0429 tracked in cargo audit config"
    ) in violations
    assert (
        f"{osv_config}: unexpected OSV ignore for RUSTSEC-2024-0413 "
        "not tracked in cargo audit config"
    ) in violations
    assert f"{osv_config}: OSV ignore for RUSTSEC-2024-0413 needs a reason" in violations


def test_supply_chain_check_rejects_trivy_exception_drift(tmp_path: Path) -> None:
    """Ensure Trivy cannot miss a Rust exception that audit and OSV allow."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_trivy_drift"
    )
    audit_config = tmp_path / "audit.toml"
    osv_config = tmp_path / "osv-scanner.toml"
    trivy_ignore = tmp_path / ".trivyignore"
    audit_config.write_text(
        """
[advisories]
ignore = ["RUSTSEC-2024-0429"]
""".strip(),
        encoding="utf-8",
    )
    osv_config.write_text(
        """
[[IgnoredVulns]]
id = "RUSTSEC-2024-0429"
reason = "glib 0.18.5 through Tauri/wry/webkit2gtk/gtk"
""".strip(),
        encoding="utf-8",
    )
    trivy_ignore.write_text("GHSA-other-placeholder\n", encoding="utf-8")

    violations = supply_chain.rust_trivy_exception_violations(
        trivy_ignore, audit_config, osv_config
    )

    assert (
        f"{trivy_ignore}: missing Trivy ignore for GHSA-wrw7-89jp-8q8g tracked as RUSTSEC-2024-0429"
    ) in violations


def test_supply_chain_check_rejects_trivy_exception_without_reason(tmp_path: Path) -> None:
    """Ensure Trivy Rust exceptions include enough removal context."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_trivy_reason"
    )
    audit_config = tmp_path / "audit.toml"
    osv_config = tmp_path / "osv-scanner.toml"
    trivy_ignore = tmp_path / ".trivyignore"
    audit_config.write_text(
        """
[advisories]
ignore = ["RUSTSEC-2024-0429"]
""".strip(),
        encoding="utf-8",
    )
    osv_config.write_text(
        """
[[IgnoredVulns]]
id = "RUSTSEC-2024-0429"
reason = "glib 0.18.5 through Tauri/wry/webkit2gtk/gtk"
""".strip(),
        encoding="utf-8",
    )
    trivy_ignore.write_text(
        """
# RUSTSEC-2024-0429 only
GHSA-wrw7-89jp-8q8g
""".strip(),
        encoding="utf-8",
    )

    violations = supply_chain.rust_trivy_exception_violations(
        trivy_ignore, audit_config, osv_config
    )

    assert (
        f"{trivy_ignore}: Trivy ignore for GHSA-wrw7-89jp-8q8g must document glib 0.18.5"
    ) in violations
    assert (
        f"{trivy_ignore}: Trivy ignore for GHSA-wrw7-89jp-8q8g must document glib >=0.20"
    ) in violations
    assert (
        f"{trivy_ignore}: Trivy ignore for GHSA-wrw7-89jp-8q8g "
        "must include an exp:YYYY-MM-DD revisit date"
    ) in violations


def test_supply_chain_check_reports_malformed_rust_exception_toml(tmp_path: Path) -> None:
    """Ensure malformed Rust exception configs produce actionable policy errors."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_osv_malformed"
    )
    audit_config = tmp_path / "audit.toml"
    osv_config = tmp_path / "osv-scanner.toml"
    audit_config.write_text("[advisories]\nignore = [", encoding="utf-8")
    osv_config.write_text("[[IgnoredVulns]]\nid = ", encoding="utf-8")

    violations = supply_chain.rust_osv_exception_violations(audit_config, osv_config)

    assert any(violation.startswith(f"{audit_config}: invalid TOML: ") for violation in violations)
    assert any(violation.startswith(f"{osv_config}: invalid TOML: ") for violation in violations)


def test_dependency_policy_documents_rust_glib_legacy_exception() -> None:
    """Ensure the glib exception records owner-chain scope and removal criteria."""
    repo_root = Path(__file__).resolve().parents[3]
    dependency_policy = repo_root / "docs" / "security" / "dependency-policy.md"
    content = dependency_policy.read_text(encoding="utf-8")

    assert "`RUSTSEC-2024-0429`" in content
    assert "`GHSA-wrw7-89jp-8q8g`" in content
    assert "for `glib 0.18.5`" in content
    assert "VariantStrIter" in content
    assert "Tauri/wry/webkit2gtk/gtk GTK3 stack" in content
    assert "A compatible lockfile refresh can move the desktop stack to" in content
    assert "`tauri 2.11.4`" in content
    assert "`wry 0.55.1`" in content
    assert "`tao 0.35.3`" in content
    assert "`muda 0.19.3`" in content
    assert "crates.io metadata for `tauri 2.11.5`" in content
    assert "Linux GTK stack is absent from the Windows and macOS artifacts" in content
    assert "Trivy" in content
    assert "drops or patches the chain" in content


def test_tauri_main_capability_uses_explicit_core_permissions() -> None:
    """Ensure Tauri core permissions stay narrow after dependency refreshes."""
    repo_root = Path(__file__).resolve().parents[3]
    capability = repo_root / "apps" / "desktop" / "src-tauri" / "capabilities" / "main.json"
    content = capability.read_text(encoding="utf-8")

    assert '"core:default"' not in content
    assert '"core:event:allow-emit"' not in content
    assert '"core:event:allow-emit-to"' not in content
    assert '"core:event:allow-listen"' in content
    assert '"core:event:allow-unlisten"' in content


def test_supply_chain_check_rejects_release_published_asset_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure immutable releases are not mutated after publication."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_immutable_release_upload"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "sbom.yml").write_text(
        """
name: sbom
on:
  release:
    types:
      - published
jobs:
  release-sbom:
    steps:
      - name: Attach SBOM to GitHub Release
        run: gh release upload "$RELEASE_TAG" bandscope-sbom.cdx.json --clobber
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert hasattr(supply_chain, "verify_immutable_release_upload_policy")
    violations = supply_chain.verify_immutable_release_upload_policy()

    assert (
        ".github/workflows/sbom.yml: release published workflows must not upload GitHub "
        "Release assets; immutable releases require draft-before-publish asset attachment"
    ) in violations


def test_supply_chain_check_accepts_immutable_release_safe_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in workflows avoid release-published asset mutation."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_immutable_release_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    assert hasattr(supply_chain, "verify_immutable_release_upload_policy")
    violations = supply_chain.verify_immutable_release_upload_policy()

    assert not violations


def test_supply_chain_check_rejects_release_artifact_wildcard_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure draft-release creation cannot attach arbitrary files from artifacts/."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_release_allowlist"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: |
          windows_amd64=(artifacts/*windows-amd64*)
      - name: Create draft release with complete assets, then publish
        run: |
          gh release create "$RELEASE_TAG" \
            artifacts/* \
            bandscope-sbom.cdx.json \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert hasattr(supply_chain, "verify_release_asset_allowlist_policy")
    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use an explicit "
        "allowlist, not artifacts/*" in violations
    )


def test_supply_chain_check_rejects_prefixed_release_artifact_wildcard_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure prefixed gh release create calls cannot bypass asset scanning."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_prefixed_release_allowlist",
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          python3 scripts/release/select_release_assets.py --input release-assets.txt
          mapfile -t release_assets < release-assets.txt
          env GH_TOKEN="$GH_TOKEN" gh release create "$RELEASE_TAG" \
            artifacts/* \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use an explicit "
        "allowlist, not artifacts/*" in violations
    )


def test_supply_chain_check_rejects_nested_shell_release_explicit_asset_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure nested shell gh release create calls cannot bypass asset scanning."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_nested_release_allowlist",
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        "\n".join(
            [
                "name: build-baseline",
                "jobs:",
                "  publish-immutable-release:",
                "    steps:",
                "      - name: Validate release asset set",
                "        run: python3 scripts/release/select_release_assets.py "
                "--output release-assets.txt",
                "      - name: Create draft release with complete assets, then publish",
                "        run: |",
                "          python3 scripts/release/select_release_assets.py "
                "--input release-assets.txt",
                "          mapfile -t release_assets < release-assets.txt",
                '          bash -c \'gh release create "$RELEASE_TAG" '
                '"${release_assets[@]}" artifacts/debug.log --draft\'',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use an explicit "
        "allowlist, not artifacts/*" in violations
    )


def test_supply_chain_check_rejects_release_asset_array_globs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure release asset arrays cannot allow matching stray platform files."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_release_array_globs"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Create draft release with complete assets, then publish
        run: |
          release_assets=(
            artifacts/*windows-amd64*.exe
            artifacts/*windows-amd64*.sha256
            bandscope-sbom.cdx.json
          )
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use an explicit "
        "allowlist, not artifacts/*" in violations
    )


def test_supply_chain_check_accepts_repo_release_asset_allowlist_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in release publishing uses the strict asset allowlist."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_release_allowlist_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert not violations


def test_supply_chain_check_requires_release_asset_revalidation_before_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure release publish revalidates the generated asset allowlist."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_release_revalidate"
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_commented_release_asset_revalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure commented revalidation commands cannot satisfy release policy."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_revalidate_comment",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          # python3 scripts/release/select_release_assets.py --input release-assets.txt
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_noop_release_asset_revalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure shell no-op revalidation commands cannot satisfy release policy."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_revalidate_noop",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          : python3 scripts/release/select_release_assets.py --input release-assets.txt
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_release_revalidation_after_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure release revalidation must happen before mapfile and publication."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_revalidate_order",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
          python3 scripts/release/select_release_assets.py --input release-assets.txt
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_requires_revalidation_for_each_release_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure every release create command is protected by revalidation."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_each_release_create_revalidation",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create protected draft release
        run: |
          python3 scripts/release/select_release_assets.py --input release-assets.txt
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
      - name: Create unprotected secondary release
        run: |
          gh release create "$SECONDARY_RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_requires_revalidation_between_same_step_release_creates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure each release create in a run block has its own revalidation."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_same_step_release_create_revalidation",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create two releases in one run step
        run: |
          python3 scripts/release/select_release_assets.py --input release-assets.txt
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
          gh release create "$SECONDARY_RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_prefixed_release_revalidation_after_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure prefixed gh release create calls still require prior revalidation."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_prefixed_release_revalidate_order",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          mapfile -t release_assets < release-assets.txt
          env GH_TOKEN="$GH_TOKEN" gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
          python3 scripts/release/select_release_assets.py --input release-assets.txt
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_release_revalidation_in_different_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure release revalidation is tied to the publishing job."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_release_revalidate_job",
    )
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  validate:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Revalidate release asset set
        run: python3 scripts/release/select_release_assets.py --input release-assets.txt
  publish-immutable-release:
    steps:
      - name: Create draft release with complete assets, then publish
        run: |
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            --draft
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use "
        "scripts/release/select_release_assets.py to generate and revalidate "
        "release-assets.txt"
    ) in violations


def test_supply_chain_check_rejects_bare_workflow_npx_package_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure workflow package execution cannot rely on bare npx package lookup."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_npx_policy"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: npx @tauri-apps/cli build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{"packages":{"node_modules/@tauri-apps/cli":{"version":"2.10.1"}}}',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert hasattr(supply_chain, "verify_workflow_npx_policy")
    violations = supply_chain.verify_workflow_npx_policy()

    assert any(
        "workflow npx package execution must use npm exec or npx --no-install: @tauri-apps/cli"
        in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_versioned_workflow_npx_package_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure npx package specs with explicit versions cannot bypass policy."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_versioned_npx"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: npx @tauri-apps/cli@2.10.1 build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_npx_policy()

    expected_violation = (
        "workflow npx package execution must use npm exec or npx --no-install: "
        "@tauri-apps/cli@2.10.1"
    )
    assert any(expected_violation in violation for violation in violations)


@pytest.mark.parametrize(
    "npx_command",
    [
        "npx -y @tauri-apps/cli build --target x86_64-pc-windows-msvc",
        "npx -y `@tauri-apps/cli` build --target x86_64-pc-windows-msvc",
        "npx '@tauri-apps/cli' build --target x86_64-pc-windows-msvc",
        'npx "@tauri-apps/cli" build --target x86_64-pc-windows-msvc',
        "npx --package @tauri-apps/cli tauri build --target x86_64-pc-windows-msvc",
        "npx --package=@tauri-apps/cli tauri build --target x86_64-pc-windows-msvc",
        "npx -p @tauri-apps/cli tauri build --target x86_64-pc-windows-msvc",
        "npx -p@tauri-apps/cli tauri build --target x86_64-pc-windows-msvc",
    ],
)
def test_supply_chain_check_rejects_workflow_npx_package_fetch_with_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npx_command: str
) -> None:
    """Ensure npx package-fetch policy cannot be bypassed with npx options."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_npx_options_policy"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        f"""
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: {npx_command}
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_npx_policy()

    assert any(
        "workflow npx package execution must use npm exec or npx --no-install: @tauri-apps/cli"
        in violation
        for violation in violations
    )


def test_supply_chain_check_allows_workflow_npx_no_install_with_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure no-install npx calls remain allowed even with other options."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_npx_no_install"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: npx --no-install -y @tauri-apps/cli build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_npx_policy()

    assert not violations


def test_supply_chain_check_rejects_late_npx_no_install_after_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure --no-install only exempts calls when it is an npx option pre-package."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_late_no_install"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: npx @tauri-apps/cli --no-install build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_npx_policy()

    assert any(
        "workflow npx package execution must use npm exec or npx --no-install: @tauri-apps/cli"
        in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_multiline_workflow_npx_package_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure multiline run blocks cannot hide npx package fetches."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_multiline_npx"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        run: |
          npx \\
            @tauri-apps/cli build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_npx_policy()

    assert any(
        "workflow npx package execution must use npm exec or npx --no-install: @tauri-apps/cli"
        in violation
        for violation in violations
    )


def test_supply_chain_check_rejects_release_create_explicit_asset_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure validated release creates cannot add hand-written asset paths."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_release_explicit_asset"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  publish-immutable-release:
    steps:
      - name: Validate release asset set
        run: python3 scripts/release/select_release_assets.py --output release-assets.txt
      - name: Create draft release with complete assets, then publish
        run: |
          mapfile -t release_assets < release-assets.txt
          gh release create "$RELEASE_TAG" \
            "${release_assets[@]}" \
            artifacts/debug.log \
            --draft
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_release_asset_allowlist_policy()

    assert (
        ".github/workflows/build-baseline.yml: release asset upload must use an explicit "
        "allowlist, not artifacts/*" in violations
    )


def test_supply_chain_check_rejects_workspace_exec_with_workflow_default_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure workflow defaults.run.working-directory cannot hide nested workspace exec."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_workflow_default_dir"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
defaults:
  run:
    working-directory: apps/desktop
jobs:
  build:
    steps:
      - name: Build native shell
        run: npm exec --workspace @bandscope/desktop -- tauri build
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_workspace_exec_policy()

    expected_violation = (
        ".github/workflows/build-baseline.yml: workflow npm exec --workspace commands must "
        "run from the repository root"
    )
    assert expected_violation in violations


def test_supply_chain_check_rejects_workspace_exec_with_job_default_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure job defaults.run.working-directory cannot hide nested workspace exec."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_job_default_dir"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    defaults:
      run:
        working-directory: apps/desktop
    steps:
      - name: Build native shell
        run: npm exec --workspace @bandscope/desktop -- tauri build
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_workspace_exec_policy()

    expected_violation = (
        ".github/workflows/build-baseline.yml: workflow npm exec --workspace commands must "
        "run from the repository root"
    )
    assert expected_violation in violations


def test_supply_chain_check_rejects_workspace_exec_from_nested_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure npm workspace commands execute from the repository root in workflows."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_workspace_exec"
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        working-directory: apps/desktop
        run: npm exec --workspace @bandscope/desktop -- tauri build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert hasattr(supply_chain, "verify_workflow_workspace_exec_policy")
    violations = supply_chain.verify_workflow_workspace_exec_policy()

    expected_violation = (
        ".github/workflows/build-baseline.yml: workflow npm exec --workspace commands must "
        "run from the repository root"
    )
    assert expected_violation in violations


def test_supply_chain_check_rejects_multiline_workspace_exec_from_nested_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure multiline npm workspace commands cannot hide nested directories."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py",
        "verify_supply_chain_multiline_workspace_exec",
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-baseline.yml").write_text(
        """
name: build-baseline
jobs:
  build:
    steps:
      - name: Build native shell
        working-directory: apps/desktop
        run: |
          npm exec \
            --workspace @bandscope/desktop -- tauri build --target x86_64-pc-windows-msvc
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    violations = supply_chain.verify_workflow_workspace_exec_policy()

    expected_violation = (
        ".github/workflows/build-baseline.yml: workflow npm exec --workspace commands must "
        "run from the repository root"
    )
    assert expected_violation in violations


def test_supply_chain_check_accepts_repo_workspace_exec_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure checked-in workflows run npm workspace execution from the root."""
    supply_chain = load_module(
        "scripts/checks/verify_supply_chain.py", "verify_supply_chain_workspace_exec_repo"
    )
    repo_root = Path(__file__).resolve().parents[3]

    monkeypatch.chdir(repo_root)

    assert hasattr(supply_chain, "verify_workflow_workspace_exec_policy")
    violations = supply_chain.verify_workflow_workspace_exec_policy()

    assert not violations


def test_opencode_review_gate_ignores_review_agent_status_contexts() -> None:
    """Ensure peer-check handling is delegated to the central OpenCode workflow."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "peer-check waits" in policy
    assert "review-agent status contexts" in policy
    assert "failed-check explanation" in policy


def test_opencode_review_unavailable_reports_provider_errors() -> None:
    """Ensure provider failure reporting is a central OpenCode workflow responsibility."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "provider/runtime failures" in policy
    assert "OpenCode runtime evidence" in policy


def test_opencode_approval_write_failure_updates_overview_only() -> None:
    """Ensure approval write failures remain central automation evidence."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "approval publication failures" in policy
    assert "automation evidence, not" in policy
    assert "source-backed repository findings" in policy


def test_pr_review_merge_scheduler_uses_central_mutation_credential() -> None:
    """Ensure mechanical PR queue handling uses the central mutation credential."""
    repo_root = Path(__file__).resolve().parents[3]
    policy = central_required_workflow_policy_text()

    opencode_config = (repo_root / "opencode.jsonc").read_text(encoding="utf-8")
    assert '"openai/o3"' in opencode_config
    assert '"openai/o4-mini"' in opencode_config
    assert_local_review_workflows_removed()
    assert "selected workflow mutation" in policy
    assert "credential, not by a maintainer's local `gh` session" in policy
    assert "PR_REVIEW_MERGE_TOKEN" in policy
    assert "OPENCODE_APPROVE_TOKEN" in policy
    assert "OpenCode GitHub App token" in policy
    assert "workflow `GITHUB_TOKEN`" in policy
    assert "update-branch, auto-merge, and merge actions" in policy


def test_opencode_review_stops_external_check_failures_without_review() -> None:
    """Ensure external check failure handling is delegated to central review automation."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "external failed-check classification" in policy
    assert "review state" in policy
    assert "current-head evidence" in policy


def test_opencode_strix_lookup_reports_missing_actions_read_scope() -> None:
    """Ensure Strix lookup token-scope diagnostics stay in central workflow policy."""
    policy = central_required_workflow_policy_text()

    assert_local_review_workflows_removed()
    assert "Strix evidence lookup" in policy
    assert "Actions read access" in policy
