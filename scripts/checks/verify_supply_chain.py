"""Verify that repository-controlled supply-chain controls stay in place."""

import functools
import re
import shlex
from datetime import date
from itertools import pairwise
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - local Python <3.11 fallback.
    import tomli as tomllib

REQUIRED_FILES = [
    Path("package-lock.json"),
    Path("services/analysis-engine/uv.lock"),
    Path("apps/desktop/src-tauri/Cargo.lock"),
    Path(".github/dependabot.yml"),
    # Dependency review runs via the org-level required workflow in
    # ContextualWisdomLab/.github; one repo-local security backstop and
    # Scorecard stay push/schedule-only while central workflows own PR scans.
    Path(".github/workflows/security-audit.yml"),
    Path(".github/workflows/sbom.yml"),
    Path(".github/workflows/release.yml"),
    Path(".github/workflows/build-baseline.yml"),
    Path(".github/workflows/ossf-scorecard.yml"),
    Path(".trivyignore"),
    Path("apps/desktop/src-tauri/osv-scanner.toml"),
    Path("docs/security/dependency-policy.md"),
    Path("docs/security/sbom-policy.md"),
    Path("docs/security/code-security.md"),
    Path("docs/security/cross-platform-build-policy.md"),
    Path("docs/security/github-required-checks.md"),
    Path("supply-chain/supplemental-component-inventory.json"),
]

PINNED_ACTION = re.compile(r"^\s*-?\s*uses:\s+[^@\s]+@[0-9a-f]{40}(\s+#.*)?$")
USES_ACTION = re.compile(r"^\s*-?\s*uses:\s+")
LOCAL_ACTION = re.compile(r"^\s*-?\s*uses:\s+\./")
DOCKER_ACTION = re.compile(r"^\s*-?\s*uses:\s+docker://")
PACKAGE_SPEC = re.compile(
    r"^(?:@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+)"
    r"(?:@[A-Za-z0-9_.~^<>=*-]+)?$"
)
NPX_FLAG_OPTIONS = {"-y", "--yes", "--ignore-existing", "--quiet"}
NPX_PACKAGE_OPTIONS = {"-p", "--package"}
NPX_VALUE_OPTIONS = {"-c", "--call", "--shell"}
OSSF_DEFAULT_BRANCH_PUBLISH_GUARD = (
    "publish_results: ${{ github.ref == format('refs/heads/{0}', "
    "github.event.repository.default_branch) }}"
)
OSSF_PUBLISH_USES_ONLY_VIOLATION = (
    "ossf scorecard publishing job must only contain uses steps; split run steps "
    "into a separate non-publishing job"
)
OSSF_PUBLISH_GLOBAL_CONFIG_VIOLATION = (
    "ossf scorecard publishing workflow must not contain top-level env or defaults"
)
OSSF_DOWNLOAD_DECOMPRESSION_VIOLATION = (
    "ossf scorecard artifact download must use skip-decompress: true and "
    "repo-owned extraction before normalization"
)
RELEASE_DOWNLOAD_DECOMPRESSION_VIOLATION = (
    "release artifact download must use skip-decompress: true and "
    "repo-owned extraction before asset validation"
)
CHECKOUT_DEFAULT_BRANCH_GUARD_VIOLATION = (
    "workflows using actions/checkout must set workflow-level "
    "GIT_CONFIG_* init.defaultBranch env to avoid Git initial-branch warnings"
)
OSSF_CHECKOUT_DEFAULT_BRANCH_GUARD_VIOLATION = (
    "ossf scorecard checkout steps must set step-level GIT_CONFIG_* "
    "init.defaultBranch env to avoid Scorecard global env/defaults"
)
CHECKOUT_DEFAULT_BRANCH_GUARD_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "init.defaultBranch",
    "GIT_CONFIG_VALUE_0": "develop",
}
OSSF_ARTIFACT_EXTRACTOR = "scripts/checks/extract_scorecard_artifact.py"
RELEASE_ARTIFACT_EXTRACTOR = "scripts/release/extract_release_artifacts.py"
OSSF_SARIF_NORMALIZER = "scripts/checks/normalize_scorecard_sarif.py"
OSSF_NORMALIZED_SARIF = "normalized-scorecard-results.sarif"
OSSF_NORMALIZED_SARIF_UPLOAD = f"sarif_file: {OSSF_NORMALIZED_SARIF}"
TRUSTED_SCORECARD_SCRIPTS_DIR = "trusted-scorecard-scripts"
OSSF_ARTIFACT_EXTRACTOR_COMMANDS = {
    OSSF_ARTIFACT_EXTRACTOR,
    f"{TRUSTED_SCORECARD_SCRIPTS_DIR}/{OSSF_ARTIFACT_EXTRACTOR}",
}
OSSF_SARIF_NORMALIZER_COMMANDS = {
    OSSF_SARIF_NORMALIZER,
    f"{TRUSTED_SCORECARD_SCRIPTS_DIR}/{OSSF_SARIF_NORMALIZER}",
}
RELEASE_ARTIFACT_GLOB = re.compile(r"(?:^|\s)artifacts/\*")
RELEASE_ASSET_VALIDATOR = "scripts/release/select_release_assets.py --output release-assets.txt"
RELEASE_ASSET_REVALIDATOR = "scripts/release/select_release_assets.py --input release-assets.txt"
RELEASE_ASSET_MAPFILE = "mapfile -t release_assets < release-assets.txt"
WORKSPACE_EXEC_PATTERN = re.compile(r"\bnpm\s+exec\s+--workspace\b")
RUST_RAND_ADVISORY_ID = "GHSA-cq8v-f236-94qc"
RUST_RAND_RETIRED_LEGACY_VERSION = "0.7.3"
RUST_RAND_PATCHED_VERSIONS = {
    (0, 8): (0, 8, 6),
    (0, 9): (0, 9, 3),
    (0, 10): (0, 10, 1),
}
RUST_GLIB_ADVISORY_ID = "RUSTSEC-2024-0429"
RUST_GLIB_TRIVY_ADVISORY_ID = "GHSA-wrw7-89jp-8q8g"
RUST_GLIB_LEGACY_EXCEPTION_VERSION = "0.18.5"
RUST_GLIB_PATCHED_VERSION = (0, 20, 0)
RUST_GLIB_LEGACY_ROOT_NAME = "tauri"
RUST_GLIB_LEGACY_EXCEPTION_PACKAGE = "glib 0.18.5"
RUST_GLIB_LEGACY_DIRECT_OWNER_NAMES = {
    "atk",
    "cairo-rs",
    "gdk",
    "gdk-pixbuf",
    "gio",
    "gtk",
    "javascriptcore-rs",
    "pango",
    "soup3",
    "webkit2gtk",
}
RUST_GLIB_LEGACY_ALLOWED_ANCESTOR_NAMES = RUST_GLIB_LEGACY_DIRECT_OWNER_NAMES | {
    "muda",
    "tao",
    RUST_GLIB_LEGACY_ROOT_NAME,
    "tauri-runtime",
    "tauri-runtime-wry",
    "wry",
}
RUST_GLIB_LEGACY_ALLOWED_APP_ROOT_NAMES = {"bandscope-desktop"}
RUST_GLIB_LEGACY_EXPECTED_CHAIN_NAMES = (
    RUST_GLIB_LEGACY_ROOT_NAME,
    "tauri-runtime-wry",
    "wry",
    "webkit2gtk",
    "gtk",
    "glib",
)
RUST_FASTRAND_YANKED_VERSION = "2.4.0"
RUST_AUDIT_CONFIG = Path("apps/desktop/src-tauri/.cargo/audit.toml")
RUST_OSV_SCANNER_CONFIG = Path("apps/desktop/src-tauri/osv-scanner.toml")
TRIVY_IGNORE_CONFIG = Path(".trivyignore")
RELEASE_CREATE_VALUE_FLAGS = {
    "--discussion-category",
    "--latest",
    "--notes",
    "--notes-file",
    "--notes-start-tag",
    "--repo",
    "--target",
    "--title",
}
RELEASE_CREATE_ALLOWED_ASSET_TOKENS = {"${release_assets[@]}", "${release_assets[*]}"}
WorkflowStepBlock = tuple[int, int, list[str]]
WorkflowRunStep = tuple[int, str, str, bool]


def workflow_step_blocks(lines: list[str]) -> list[WorkflowStepBlock]:
    """Return YAML step blocks nested under a workflow ``steps:`` parent."""
    step_blocks: list[WorkflowStepBlock] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        step_indent = len(line) - len(line.lstrip(" "))
        if step_indent < 6:
            continue
        has_steps_parent = False
        for previous_line in reversed(lines[:index]):
            previous_stripped = previous_line.strip().partition("#")[0].strip()
            previous_indent = len(previous_line) - len(previous_line.lstrip(" "))
            if previous_indent >= step_indent:
                continue
            if previous_stripped == "steps:":
                has_steps_parent = True
            break
        if not has_steps_parent:
            continue
        step_lines = [line]
        for following_line in lines[index + 1 :]:
            following_stripped = following_line.strip()
            following_indent = len(following_line) - len(following_line.lstrip(" "))
            if following_stripped.startswith("- ") and following_indent <= step_indent:
                break
            step_lines.append(following_line)
        step_blocks.append((index, step_indent, step_lines))
    return step_blocks


def workflow_job_content_for_step(lines: list[str], line_index: int) -> str:
    """Return the workflow job block containing ``line_index``."""
    job_start = 0
    for reverse_index in range(line_index, -1, -1):
        candidate = lines[reverse_index]
        candidate_without_comment = candidate.strip().partition("#")[0].strip()
        if len(candidate) - len(candidate.lstrip(" ")) == 2 and candidate_without_comment.endswith(
            ":"
        ):
            job_start = reverse_index
            break
    job_end = len(lines)
    for forward_index in range(job_start + 1, len(lines)):
        candidate = lines[forward_index]
        candidate_without_comment = candidate.strip().partition("#")[0].strip()
        if len(candidate) - len(candidate.lstrip(" ")) == 2 and candidate_without_comment.endswith(
            ":"
        ):
            job_end = forward_index
            break
    return "\n".join(lines[job_start:job_end])


def step_run_command_from_block(step_lines: list[str], step_indent: int) -> str:
    """Return a workflow step run command with comments and YAML wrappers removed."""
    run_indent: int | None = None
    command_lines: list[str] = []
    for step_line in step_lines:
        raw_stripped = step_line.strip()
        yaml_stripped = raw_stripped.partition("#")[0].strip()
        stripped = yaml_stripped
        is_step_start = stripped.startswith("- ")
        if is_step_start:
            stripped = stripped[2:].strip()
        indent = len(step_line) - len(step_line.lstrip(" "))
        if run_indent is None:
            if stripped.startswith("run:") and (indent > step_indent or is_step_start):
                run_indent = indent
                run_value = stripped.partition(":")[2].strip()
                command_lines.append("" if run_value in {"|", "|-", ">", ">-"} else run_value)
            continue
        stripped = "" if raw_stripped.startswith("#") else raw_stripped
        if stripped and indent <= run_indent:
            break
        command_lines.append(stripped)
    return "\n".join(command_lines)


def workflow_run_steps(content: str) -> list[WorkflowRunStep]:
    """Return run commands with their workflow job content and blocking status."""
    lines = content.splitlines()
    run_steps: list[WorkflowRunStep] = []
    for index, step_indent, step_lines in workflow_step_blocks(lines):
        command = step_run_command_from_block(step_lines, step_indent)
        if not command.strip():
            continue
        is_blocking = step_is_blocking(step_lines, step_indent)
        run_steps.append(
            (
                index,
                workflow_job_content_for_step(lines, index),
                command,
                is_blocking,
            )
        )
    return run_steps


def step_with_value_from_block(step_lines: list[str], step_indent: int, key: str) -> str | None:
    """Return a workflow step ``with`` value for ``key`` when scoped under with."""
    with_indent: int | None = None
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(?P<value>.*?)\s*$")
    for step_line in step_lines:
        stripped = step_line.partition("#")[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(step_line) - len(step_line.lstrip(" "))
        if with_indent is None:
            if indent > step_indent and stripped.strip() == "with:":
                with_indent = indent
            continue
        if indent <= with_indent:
            break
        match = key_pattern.match(stripped)
        if match:
            return match.group("value").strip().strip("'\"")
    return None


def step_env_from_block(step_lines: list[str], step_indent: int) -> dict[str, str]:
    """Return a workflow step ``env`` mapping from a step block."""
    env: dict[str, str] = {}
    env_indent: int | None = None
    for step_line in step_lines:
        stripped = step_line.partition("#")[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(step_line) - len(step_line.lstrip(" "))
        if env_indent is None:
            if indent > step_indent and stripped.strip() == "env:":
                env_indent = indent
            continue
        if indent <= env_indent:
            break
        match = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", stripped)
        if match is None:
            continue
        env[match.group(1)] = match.group(2).strip().strip("\"'")
    return env


def step_scalar_value_from_block(step_lines: list[str], step_indent: int, key: str) -> str | None:
    """Return a simple top-level scalar value from a workflow step block."""
    for step_line in step_lines:
        stripped = step_line.partition("#")[0].strip()
        if not stripped:
            continue
        if stripped.startswith(f"- {key}:"):
            return yaml_scalar_value(stripped[2:].strip())
        indent = len(step_line) - len(step_line.lstrip(" "))
        if indent == step_indent + 2 and stripped.startswith(f"{key}:"):
            return yaml_scalar_value(stripped)
    return None


def step_is_blocking(step_lines: list[str], step_indent: int) -> bool:
    """Return whether a workflow step should block when its command fails."""
    continue_on_error = step_scalar_value_from_block(step_lines, step_indent, "continue-on-error")
    if continue_on_error is None:
        return True
    normalized = re.sub(r"\s+", "", continue_on_error.casefold())
    return normalized in {"false", "${{false}}"}


def step_is_required_blocking(step_lines: list[str], step_indent: int) -> bool:
    """Return whether a workflow step is unconditional and failure-blocking."""
    if step_scalar_value_from_block(step_lines, step_indent, "if") is not None:
        return False
    return step_is_blocking(step_lines, step_indent)


def logical_workflow_lines(content: str) -> list[tuple[int, str]]:
    """Return workflow lines with shell backslash continuations folded."""
    logical_lines: list[tuple[int, str]] = []
    pending_parts: list[str] = []
    pending_start = 0
    for idx, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped and not pending_parts:
            continue

        if not pending_parts:
            pending_start = idx

        if stripped.endswith("\\"):
            part = stripped[:-1].rstrip()
            if part:
                pending_parts.append(part)
        else:
            pending_parts.append(stripped)
            logical_lines.append((pending_start, " ".join(pending_parts)))
            pending_parts.clear()
            pending_start = 0

    if pending_parts:
        logical_lines.append((pending_start, " ".join(pending_parts)))
    return logical_lines


def shell_logical_lines(command: str) -> list[str]:
    """Return shell command lines with backslash continuations folded."""
    logical_lines = [line for _, line in logical_workflow_lines(command)]
    return logical_lines or [command]


def shell_line_tokens(line: str) -> list[str]:
    """Return shell tokens for a logical command line."""
    try:
        return shlex.split(line, comments=True)
    except ValueError:
        return line.split("#", maxsplit=1)[0].split()


def nested_shell_commands(tokens: list[str]) -> list[str]:
    """Return shell -c command strings embedded in a tokenized command line."""
    nested_commands: list[str] = []
    shell_names = {"bash", "dash", "sh", "zsh"}
    for index, token in enumerate(tokens):
        if token.rsplit("/", maxsplit=1)[-1] not in shell_names:
            continue
        for option_index in range(index + 1, len(tokens)):
            option = tokens[option_index]
            if option == "-c" or (
                option.startswith("-") and not option.startswith("--") and "c" in option[1:]
            ):
                if option_index + 1 < len(tokens):
                    nested_commands.append(tokens[option_index + 1])
                break
            if not option.startswith("-"):
                break
    return nested_commands


def is_shell_assignment_token(token: str) -> bool:
    """Return whether a token is a shell variable assignment prefix."""
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token) is not None


def strip_shell_assignment_prefix(tokens: list[str]) -> list[str]:
    """Return tokens after leading shell assignment prefixes."""
    index = 0
    while index < len(tokens) and is_shell_assignment_token(tokens[index]):
        index += 1
    return tokens[index:]


def env_wrapped_command_tokens(tokens: list[str]) -> list[str]:
    """Return the command portion of an env-wrapped command line."""
    index = 0
    options_with_values = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1 :]
        if is_shell_assignment_token(token):
            index += 1
            continue
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return tokens[index:]
    return []


def uv_run_command_tokens(tokens: list[str]) -> list[str]:
    """Return the command portion of a uv run invocation."""
    index = 0
    options_with_values = {
        "--config-file",
        "--default-index",
        "--directory",
        "--env-file",
        "--exclude-newer",
        "--extra-index-url",
        "--index",
        "--index-strategy",
        "--index-url",
        "--keyring-provider",
        "--link-mode",
        "--managed-python",
        "--project",
        "--python",
        "--resolution",
        "--with",
        "--with-editable",
        "--with-requirements",
    }
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1 :]
        if token in options_with_values:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in options_with_values):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return tokens[index:]
    return []


def command_tokens_start_with_sequence(
    tokens: list[str], expected_tokens: list[str], *, recursion_depth: int
) -> bool:
    """Return whether a tokenized shell command executes the expected prefix."""
    tokens = strip_shell_assignment_prefix(tokens)
    if not tokens:
        return False

    executable = tokens[0].rsplit("/", maxsplit=1)[-1]
    if executable in {":", "echo", "printf"}:
        return False
    if executable == "env":
        return command_tokens_start_with_sequence(
            env_wrapped_command_tokens(tokens[1:]),
            expected_tokens,
            recursion_depth=recursion_depth,
        )
    if executable == "uv" and len(tokens) > 1 and tokens[1] == "run":
        return command_tokens_start_with_sequence(
            uv_run_command_tokens(tokens[2:]),
            expected_tokens,
            recursion_depth=recursion_depth,
        )
    if executable in {"python", "python3"}:
        return tokens[1 : 1 + len(expected_tokens)] == expected_tokens
    if executable in {"bash", "dash", "sh", "zsh"}:
        if recursion_depth >= 2:
            return False
        return any(
            command_contains_token_sequence(
                nested_command,
                " ".join(shlex.quote(token) for token in expected_tokens),
                recursion_depth=recursion_depth + 1,
            )
            for nested_command in nested_shell_commands(tokens)
        )

    return tokens[: len(expected_tokens)] == expected_tokens


def command_contains_token_sequence(
    command: str, token_sequence: str, *, recursion_depth: int = 0
) -> bool:
    """Return whether a run command executes the requested token sequence."""
    expected_tokens = shell_line_tokens(token_sequence)
    if not expected_tokens:
        return False
    for line in shell_logical_lines(command):
        tokens = shell_line_tokens(line)
        if tokens and tokens[0] in {"echo", "printf"}:
            continue
        if command_tokens_start_with_sequence(
            tokens, expected_tokens, recursion_depth=recursion_depth
        ):
            return True
    return False


def executed_command_token_lists(tokens: list[str], *, recursion_depth: int = 0) -> list[list[str]]:
    """Return tokenized commands after unwrapping allowed command wrappers."""
    tokens = strip_shell_assignment_prefix(tokens)
    if not tokens:
        return []

    executable = tokens[0].rsplit("/", maxsplit=1)[-1]
    if executable in {":", "echo", "printf"}:
        return []
    if executable == "env":
        return executed_command_token_lists(
            env_wrapped_command_tokens(tokens[1:]), recursion_depth=recursion_depth
        )
    if executable == "uv" and len(tokens) > 1 and tokens[1] == "run":
        return executed_command_token_lists(
            uv_run_command_tokens(tokens[2:]), recursion_depth=recursion_depth
        )
    if executable in {"bash", "dash", "sh", "zsh"}:
        if recursion_depth >= 2:
            return []
        nested_commands: list[list[str]] = []
        for nested_command in nested_shell_commands(tokens):
            for nested_line in shell_logical_lines(nested_command):
                nested_commands.extend(
                    executed_command_token_lists(
                        shell_line_tokens(nested_line),
                        recursion_depth=recursion_depth + 1,
                    )
                )
        return nested_commands
    return [tokens]


def yaml_scalar_value(stripped_line: str) -> str:
    """Return a simple YAML scalar value after the first colon."""
    return stripped_line.partition(":")[2].strip().strip("\"'")


def clean_package_token(token: str) -> str:
    """Return a normalized package token stripped of shell quoting wrappers."""
    return token.strip().strip("`").strip()


def repo_display_path(path: Path) -> str:
    """Return repo-relative paths in the slash form used by GitHub logs."""
    return path.as_posix()


def npx_package_from_command(command: str) -> str | None:
    """Return the package fetched by an unsafe npx command, when present."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for npx_index, token in enumerate(tokens):
        if token != "npx":
            continue
        no_install = False
        idx = npx_index + 1
        while idx < len(tokens):
            current = tokens[idx]
            if current == "--no-install":
                no_install = True
                idx += 1
                continue
            if current in NPX_FLAG_OPTIONS:
                idx += 1
                continue
            if current in NPX_PACKAGE_OPTIONS:
                if idx + 1 >= len(tokens):
                    return None
                package = clean_package_token(tokens[idx + 1])
                return None if no_install else package
            if current.startswith("--package="):
                package = clean_package_token(current.partition("=")[2])
                return None if no_install else package
            if current.startswith("-p") and current != "-p":
                package = clean_package_token(current[2:])
                return None if no_install else package
            if current in NPX_VALUE_OPTIONS:
                idx += 2
                continue
            if current.startswith("--") and "=" in current:
                idx += 1
                continue
            if current.startswith("-"):
                idx += 1
                continue
            package = clean_package_token(current)
            if PACKAGE_SPEC.fullmatch(package) is None:
                return None
            return None if no_install else package
    return None


def release_asset_allowlist_violation(path: Path) -> str:
    """Return the standard release asset allowlist violation for a workflow."""
    return (
        f"{repo_display_path(path)}: release asset upload must use an "
        "explicit allowlist, not artifacts/*"
    )


def add_release_asset_allowlist_violation(violations: list[str], path: Path) -> None:
    """Append the release asset allowlist violation once per workflow."""
    violation = release_asset_allowlist_violation(path)
    if violation not in violations:
        violations.append(violation)


def release_create_explicit_asset_tokens_from_tokens(tokens: list[str]) -> list[str]:
    """Return non-allowlisted asset tokens from tokenized ``gh release create``."""
    command_index = -1
    for idx in range(len(tokens) - 2):
        if tokens[idx : idx + 3] == ["gh", "release", "create"]:
            command_index = idx
            break
    if command_index < 0:
        return []

    explicit_assets: list[str] = []
    seen_tag = False
    idx = command_index + 3
    while idx < len(tokens):
        token = tokens[idx]
        if token == "--":
            explicit_assets.extend(tokens[idx + 1 :])
            break
        if token.startswith("--"):
            flag_name = token.split("=", maxsplit=1)[0]
            if "=" not in token and flag_name in RELEASE_CREATE_VALUE_FLAGS:
                idx += 2
            else:
                idx += 1
            continue
        if token.startswith("-"):
            idx += 1
            continue
        if not seen_tag:
            seen_tag = True
            idx += 1
            continue
        if token in RELEASE_CREATE_ALLOWED_ASSET_TOKENS:
            idx += 1
            continue
        explicit_assets.append(token)
        idx += 1
    return explicit_assets


def release_create_explicit_asset_tokens(command: str) -> list[str]:
    """Return non-allowlisted asset tokens from a gh release create command."""
    explicit_assets: list[str] = []
    for line in shell_logical_lines(command):
        try:
            tokens = shlex.split(line)
        except ValueError:
            explicit_assets.append(line)
            continue
        for executed_tokens in executed_command_token_lists(tokens):
            explicit_assets.extend(
                release_create_explicit_asset_tokens_from_tokens(executed_tokens)
            )
    return explicit_assets


def verify_required_files() -> list[str]:
    """Return missing files required by the supply-chain baseline."""
    return [str(path) for path in REQUIRED_FILES if not path.exists()]


def verify_pinned_actions() -> list[str]:
    """Return workflow actions that are not pinned to immutable SHAs."""
    violations: list[str] = []
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for path in workflow_paths:
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if USES_ACTION.match(line) is None:
                continue
            if PINNED_ACTION.match(line) or LOCAL_ACTION.match(line) or DOCKER_ACTION.match(line):
                continue
            violations.append(
                f"{repo_display_path(path)}:{idx} -> workflow action must be pinned by SHA"
            )
    return violations


def workflow_top_level_env(content: str) -> dict[str, str]:
    """Return the simple top-level env mapping from a GitHub Actions workflow."""
    env: dict[str, str] = {}
    lines = content.splitlines()
    for index, line in enumerate(lines):
        line_without_comment = line.partition("#")[0].rstrip()
        if line_without_comment != "env:":
            continue
        child_indent: int | None = None
        for env_line in lines[index + 1 :]:
            env_line_without_comment = env_line.partition("#")[0].rstrip()
            if not env_line_without_comment.strip():
                continue
            indent = len(env_line_without_comment) - len(env_line_without_comment.lstrip(" "))
            if indent == 0:
                break
            if child_indent is None:
                child_indent = indent
            if indent != child_indent:
                continue
            match = re.match(
                r"^\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$",
                env_line_without_comment,
            )
            if match is None:
                continue
            value = match.group(2).strip().strip("\"'")
            env[match.group(1)] = value
        break
    return env


def workflow_top_level_key_lines(content: str, keys: set[str]) -> list[tuple[int, str]]:
    """Return top-level workflow key line numbers for ``keys``."""
    key_lines: list[tuple[int, str]] = []
    for idx, line in enumerate(content.splitlines(), start=1):
        line_without_comment = line.partition("#")[0].rstrip()
        if not line_without_comment.strip():
            continue
        if len(line_without_comment) - len(line_without_comment.lstrip(" ")) != 0:
            continue
        key = line_without_comment.partition(":")[0].strip()
        if key in keys:
            key_lines.append((idx, key))
    return key_lines


def workflow_publishes_scorecard_results(content: str) -> bool:
    """Return whether a workflow publishes OSSF Scorecard results."""
    workflow_body = "\n".join(line.partition("#")[0] for line in content.splitlines())
    return "ossf/scorecard-action" in workflow_body and "publish_results:" in workflow_body


def checkout_step_has_default_branch_guard(step_lines: list[str], step_indent: int) -> bool:
    """Return whether a checkout step carries the Git default branch env guard."""
    env = step_env_from_block(step_lines, step_indent)
    return all(env.get(key) == value for key, value in CHECKOUT_DEFAULT_BRANCH_GUARD_ENV.items())


def verify_checkout_default_branch_guard() -> list[str]:
    """Return checkout workflows missing the Git default-branch warning guard."""
    violations: list[str] = []
    checkout_uses_pattern = re.compile(r"^\s*-?\s*uses:\s*(?:[\"'])?actions/checkout@")
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for path in workflow_paths:
        content = path.read_text(encoding="utf-8")
        has_checkout = any(
            checkout_uses_pattern.search(line.partition("#")[0]) for line in content.splitlines()
        )
        if not has_checkout:
            continue
        if workflow_publishes_scorecard_results(content):
            checkout_steps = [
                (step_indent, step_lines)
                for _, step_indent, step_lines in workflow_step_blocks(content.splitlines())
                if any(
                    checkout_uses_pattern.search(step_line.partition("#")[0])
                    for step_line in step_lines
                )
            ]
            if all(
                checkout_step_has_default_branch_guard(step_lines, step_indent)
                for step_indent, step_lines in checkout_steps
            ):
                continue
            violations.append(
                f"{repo_display_path(path)}: {OSSF_CHECKOUT_DEFAULT_BRANCH_GUARD_VIOLATION}"
            )
            continue
        env = workflow_top_level_env(content)
        if all(env.get(key) == value for key, value in CHECKOUT_DEFAULT_BRANCH_GUARD_ENV.items()):
            continue
        violations.append(f"{repo_display_path(path)}: {CHECKOUT_DEFAULT_BRANCH_GUARD_VIOLATION}")
    return violations


def verify_dependabot_coverage() -> list[str]:
    """Return missing Dependabot ecosystems from the repo configuration."""
    path = Path(".github/dependabot.yml")
    if not path.exists():
        return [f"missing file: {path}"]
    content = path.read_text(encoding="utf-8")
    missing: list[str] = []
    for ecosystem in ["npm", "pip", "cargo", "github-actions"]:
        if f'package-ecosystem: "{ecosystem}"' not in content:
            missing.append(f"dependabot missing ecosystem: {ecosystem}")
    return missing


def read_workflow(
    path: Path, label: str, missing: list[str], *, optional: bool = False
) -> str:
    """Read a workflow file, recording a missing-file violation when absent.

    Centralized governance controls (dependency review, CodeQL, OSSF Scorecard)
    are provided by the org-level required workflows in ContextualWisdomLab/
    .github, so this repository intentionally carries no local copies. Pass
    ``optional=True`` for those controls: an absent local file is skipped rather
    than flagged, while any local copy that is present is still fully validated.
    """
    if not path.exists():
        if not optional:
            missing.append(f"missing file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def ossf_scorecard_publish_restriction_violations(
    content: str, path: Path | None = None
) -> list[str]:
    """Return OSSF publishing job violations that GitHub cannot publish."""
    violations: list[str] = []
    current_job_lines: list[str] = []
    current_job_start_line = 0
    in_jobs = False

    def evaluate_job(job_lines: list[str], start_line: int) -> None:
        if not job_lines:
            return
        job_content = "\n".join(job_lines)
        if "ossf/scorecard-action" not in job_content:
            return
        if "publish_results:" not in job_content:
            return
        has_run_step = any(
            stripped.startswith("run:") or re.match(r"^-\s+run:", stripped)
            for stripped in (line.strip() for line in job_lines)
        )
        if has_run_step:
            if path is None:
                violations.append(OSSF_PUBLISH_USES_ONLY_VIOLATION)
            else:
                violations.append(
                    f"{repo_display_path(path)}:{start_line or 1} -> "
                    f"{OSSF_PUBLISH_USES_ONLY_VIOLATION}"
                )

    if workflow_publishes_scorecard_results(content):
        for line_number, _ in workflow_top_level_key_lines(content, {"env", "defaults"}):
            if path is None:
                violations.append(OSSF_PUBLISH_GLOBAL_CONFIG_VIOLATION)
            else:
                violations.append(
                    f"{repo_display_path(path)}:{line_number} -> "
                    f"{OSSF_PUBLISH_GLOBAL_CONFIG_VIOLATION}"
                )

    for idx, line in enumerate(content.splitlines(), start=1):
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if indent == 0 and stripped:
            evaluate_job(current_job_lines, current_job_start_line)
            current_job_lines = []
            current_job_start_line = 0
            in_jobs = False
            continue
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            evaluate_job(current_job_lines, current_job_start_line)
            current_job_lines = [line]
            current_job_start_line = idx
            continue
        current_job_lines.append(line)

    evaluate_job(current_job_lines, current_job_start_line)
    return violations


def scorecard_sarif_upload_normalization_violations(content: str) -> list[str]:
    """Return Scorecard SARIF upload steps that bypass the normalizer output."""
    if "ossf/scorecard-action" not in content:
        return []
    if "github/codeql-action/upload-sarif" not in content:
        return []

    def upload_step_sarif_file(step_lines: list[str], step_indent: int) -> str | None:
        with_indent: int | None = None
        for step_line in step_lines:
            raw_stripped = step_line.strip().partition("#")[0].strip()
            stripped = raw_stripped
            is_step_start = stripped.startswith("- ")
            if is_step_start:
                stripped = stripped[2:].strip()
            indent = len(step_line) - len(step_line.lstrip(" "))
            if with_indent is None:
                if stripped == "with:" and (indent > step_indent or is_step_start):
                    with_indent = indent
                continue
            if stripped and indent <= with_indent:
                break
            if stripped.startswith("sarif_file:") and indent > with_indent:
                return stripped.partition(":")[2].partition("#")[0].strip().strip("'\"")
        return None

    def normalizer_output_file(command: str) -> str | None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = re.split(r"\s+", command)
        cleaned_tokens = [token.strip("'\"") for token in tokens if token.strip("'\"")]
        if cleaned_tokens and cleaned_tokens[0] in {">", ">-", "|", "|-"}:
            cleaned_tokens = cleaned_tokens[1:]
        if len(cleaned_tokens) < 4:
            return None
        if cleaned_tokens[0] not in {"python", "python3"}:
            return None
        if cleaned_tokens[1] not in OSSF_SARIF_NORMALIZER_COMMANDS:
            return None
        positional_args = cleaned_tokens[2:]
        if len(positional_args) < 2:
            return None
        return positional_args[1]

    def workflow_job_step_blocks(line_index: int) -> list[tuple[int, int, list[str]]]:
        job_content = workflow_job_content_for_step(lines, line_index)
        return [
            block
            for block in step_blocks
            if workflow_job_content_for_step(lines, block[0]) == job_content
        ]

    lines = content.splitlines()

    step_blocks = workflow_step_blocks(lines)

    violations: list[str] = []
    for index, step_indent, step_lines in step_blocks:
        if "github/codeql-action/upload-sarif" not in "\n".join(
            line.partition("#")[0] for line in step_lines
        ):
            continue
        sarif_file = upload_step_sarif_file(step_lines, step_indent)
        job_content = workflow_job_content_for_step(lines, index)
        job_content_without_comments = "\n".join(
            line.partition("#")[0] for line in job_content.splitlines()
        )
        job_blocks = workflow_job_step_blocks(index)
        normalizer_run_commands = [
            step_run_command_from_block(normalizer_step_lines, normalizer_step_indent)
            for normalizer_index, normalizer_step_indent, normalizer_step_lines in job_blocks
            if normalizer_index < index
            and step_is_required_blocking(normalizer_step_lines, normalizer_step_indent)
        ]
        normalizer_outputs = {
            output
            for command in normalizer_run_commands
            if (output := normalizer_output_file(command)) is not None
        }
        job_has_scorecard_artifact_source = (
            "ossf/scorecard-action" in job_content_without_comments
            or (
                "actions/download-artifact" in job_content_without_comments
                and "ossf-scorecard-results" in job_content_without_comments
            )
        )
        scorecard_sarif_upload = sarif_file == OSSF_NORMALIZED_SARIF or (
            sarif_file is not None
            and (
                "scorecard" in sarif_file
                or (
                    sarif_file == "results.sarif"
                    and "ossf/scorecard-action" in job_content_without_comments
                )
            )
        )
        if not scorecard_sarif_upload:
            continue
        if (
            job_has_scorecard_artifact_source
            and sarif_file is not None
            and sarif_file in normalizer_outputs
        ):
            continue
        violations.append(
            "ossf scorecard SARIF upload must normalize repository-level "
            "placeholder URIs before upload-sarif"
        )
    return violations


def scorecard_artifact_download_decompression_violations(content: str) -> list[str]:
    """Return Scorecard downloads that rely on action-owned ZIP decompression."""
    content_without_comments = "\n".join(line.partition("#")[0] for line in content.splitlines())
    if "actions/download-artifact" not in content_without_comments:
        return []
    if "ossf-scorecard-results" not in content_without_comments:
        return []

    lines = content.splitlines()
    step_blocks = workflow_step_blocks(lines)

    def invokes_scorecard_extractor(command: str) -> bool:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = re.split(r"\s+", command)
        cleaned_tokens = [token.strip("'\"") for token in tokens if token.strip("'\"")]
        if cleaned_tokens and cleaned_tokens[0] in {">", ">-", "|", "|-"}:
            cleaned_tokens = cleaned_tokens[1:]
        return (
            len(cleaned_tokens) == 4
            and cleaned_tokens[0] in {"python", "python3"}
            and cleaned_tokens[1] in OSSF_ARTIFACT_EXTRACTOR_COMMANDS
            and cleaned_tokens[2] == "scorecard-artifact"
            and cleaned_tokens[3] == "scorecard-sarif"
        )

    violations: list[str] = []
    for index, block_indent, step_lines in step_blocks:
        step_content = "\n".join(line.partition("#")[0] for line in step_lines)
        if "actions/download-artifact" not in step_content:
            continue
        if "ossf-scorecard-results" not in step_content:
            continue
        if step_with_value_from_block(step_lines, block_indent, "skip-decompress") != "true":
            violations.append(OSSF_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue

        job_content = workflow_job_content_for_step(lines, index)
        job_step_blocks = [
            block
            for block in step_blocks
            if workflow_job_content_for_step(lines, block[0]) == job_content
        ]
        later_steps = [
            (block_indent, block_lines)
            for block_index, block_indent, block_lines in job_step_blocks
            if block_index > index
        ]
        extractor_step_position = next(
            (
                position
                for position, (block_indent, block_lines) in enumerate(later_steps)
                if invokes_scorecard_extractor(
                    step_run_command_from_block(block_lines, block_indent)
                )
            ),
            None,
        )
        normalizer_step_position = next(
            (
                position
                for position, (block_indent, block_lines) in enumerate(later_steps)
                if (OSSF_SARIF_NORMALIZER in step_run_command_from_block(block_lines, block_indent))
            ),
            None,
        )
        if extractor_step_position is None:
            violations.append(OSSF_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue
        if (
            normalizer_step_position is not None
            and extractor_step_position > normalizer_step_position
        ):
            violations.append(OSSF_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue
    if violations:
        return [OSSF_DOWNLOAD_DECOMPRESSION_VIOLATION]
    return []


def release_artifact_download_decompression_violations(content: str) -> list[str]:
    """Return release downloads that rely on action-owned ZIP decompression."""
    content_without_comments = "\n".join(line.partition("#")[0] for line in content.splitlines())
    if "actions/download-artifact" not in content_without_comments:
        return []
    if "bandscope-*-${{ github.sha }}" not in content_without_comments:
        return []

    lines = content.splitlines()
    step_blocks = workflow_step_blocks(lines)

    def invokes_release_extractor(command: str) -> bool:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = re.split(r"\s+", command)
        cleaned_tokens = [token.strip("'\"") for token in tokens if token.strip("'\"")]
        if cleaned_tokens and cleaned_tokens[0] in {">", ">-", "|", "|-"}:
            cleaned_tokens = cleaned_tokens[1:]
        return (
            len(cleaned_tokens) == 4
            and cleaned_tokens[0] in {"python", "python3"}
            and cleaned_tokens[1] == RELEASE_ARTIFACT_EXTRACTOR
            and cleaned_tokens[2] == "downloaded-artifacts"
            and cleaned_tokens[3] == "artifacts"
        )

    def is_blocking_required_step(block_lines: list[str], block_indent: int) -> bool:
        return step_is_required_blocking(block_lines, block_indent)

    violations: list[str] = []
    for index, block_indent, step_lines in step_blocks:
        step_content = "\n".join(line.partition("#")[0] for line in step_lines)
        if "actions/download-artifact" not in step_content:
            continue
        if "bandscope-*-${{ github.sha }}" not in step_content:
            continue
        if step_with_value_from_block(step_lines, block_indent, "skip-decompress") != "true":
            violations.append(RELEASE_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue

        job_content = workflow_job_content_for_step(lines, index)
        job_step_blocks = [
            block
            for block in step_blocks
            if workflow_job_content_for_step(lines, block[0]) == job_content
        ]
        later_steps = [
            (block_indent, block_lines)
            for block_index, block_indent, block_lines in job_step_blocks
            if block_index > index
        ]
        extractor_step_position = next(
            (
                position
                for position, (block_indent, block_lines) in enumerate(later_steps)
                if invokes_release_extractor(step_run_command_from_block(block_lines, block_indent))
                and is_blocking_required_step(block_lines, block_indent)
            ),
            None,
        )
        validator_step_position = next(
            (
                position
                for position, (block_indent, block_lines) in enumerate(later_steps)
                if (
                    RELEASE_ASSET_VALIDATOR
                    in step_run_command_from_block(block_lines, block_indent)
                )
            ),
            None,
        )
        if extractor_step_position is None:
            violations.append(RELEASE_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue
        if (
            validator_step_position is not None
            and extractor_step_position > validator_step_position
        ):
            violations.append(RELEASE_DOWNLOAD_DECOMPRESSION_VIOLATION)
            continue
    if violations:
        return [RELEASE_DOWNLOAD_DECOMPRESSION_VIOLATION]
    return []


def _verify_ci_coverage(missing: list[str]) -> None:
    ci = read_workflow(Path(".github/workflows/ci.yml"), "ci", missing)
    for token in ["develop", "main", "pull_request", "push", "ci / build-and-test"]:
        if ci and token not in ci:
            missing.append(f"ci workflow missing token: {token}")


def _verify_sbom_coverage(missing: list[str]) -> None:
    sbom = read_workflow(Path(".github/workflows/sbom.yml"), "sbom", missing)
    for token in ["develop", "main", "pull_request", "release:", "tags:"]:
        if sbom and token not in sbom:
            missing.append(f"sbom workflow missing trigger token: {token}")


def _verify_dependency_review_coverage(missing: list[str]) -> None:
    review = read_workflow(
        Path(".github/workflows/dependency-review.yml"),
        "dependency review",
        missing,
        optional=True,
    )
    for token in ["develop", "main", "pull_request"]:
        if review and token not in review:
            missing.append(f"dependency review workflow missing trigger token: {token}")


def _verify_security_audit_coverage(missing: list[str]) -> None:
    audit = read_workflow(Path(".github/workflows/security-audit.yml"), "security audit", missing)
    for token in ["develop", "main", "push", "bandit", "git grep", "trivy-action"]:
        if audit and token not in audit:
            missing.append(f"security audit workflow missing trigger token: {token}")
    audit_run_commands: list[str] = []
    if audit:
        for _, step_indent, step_lines in workflow_step_blocks(audit.splitlines()):
            if not step_is_required_blocking(step_lines, step_indent):
                continue
            command = step_run_command_from_block(step_lines, step_indent)
            if command.strip():
                audit_run_commands.append(command)
    for token in [
        "npm audit --workspaces --audit-level=high",
        "pip-audit --local --strict",
        "cargo +stable audit",
    ]:
        if audit and not any(
            command_contains_token_sequence(command, token) for command in audit_run_commands
        ):
            missing.append(f"security audit workflow missing vulnerability audit token: {token}")


def _verify_bandit_coverage(missing: list[str]) -> None:
    bandit = read_workflow(Path(".github/workflows/security-audit.yml"), "bandit", missing)
    for token in ["develop", "main", "push", "bandit"]:
        if bandit and token not in bandit:
            missing.append(f"bandit workflow missing token: {token}")
    if bandit and "pull_request:" in bandit:
        missing.append(
            "bandit workflow must stay push/manual-only; central SAST owns PR scanning"
        )


def _verify_codeql_coverage(missing: list[str]) -> None:
    if Path(".github/workflows/codeql.yml").exists():
        missing.append("repo-local codeql workflow duplicates GitHub default setup")


def _verify_release_coverage(missing: list[str]) -> None:
    release = read_workflow(Path(".github/workflows/release.yml"), "release", missing)
    for token in [
        "develop",
        "main",
        "push",
        "tags:",
        "release-preflight",
    ]:
        if release and token not in release:
            missing.append(f"release workflow missing token: {token}")


def _verify_secret_scan_coverage(missing: list[str]) -> None:
    secret_scan = read_workflow(
        Path(".github/workflows/security-audit.yml"), "secret scan", missing
    )
    for token in ["develop", "main", "push", "git grep"]:
        if secret_scan and token not in secret_scan:
            missing.append(f"secret scan workflow missing token: {token}")


def _verify_build_coverage(missing: list[str]) -> None:
    build = read_workflow(Path(".github/workflows/build-baseline.yml"), "build baseline", missing)
    for token in [
        "develop",
        "main",
        "pull_request",
        "push",
        "tags:",
        "windows-2025",
        "windows-11-arm",
        "macos-15-intel",
        "macos-15",
        "gate / build / windows",
        "gate / build / macos",
        "release-artifact / publish",
        "ubuntu-latest",
        "bandscope-windows-amd64-${{ github.sha }}",
        "bandscope-windows-arm64-${{ github.sha }}",
        "bandscope-macos-amd64-${{ github.sha }}",
        "bandscope-macos-arm64-${{ github.sha }}",
        "bandscope-release-sbom-${{ github.sha }}",
        "gh release create",
        "--draft",
        "--verify-tag",
        "Get-MpComputerStatus",
    ]:
        if build and token not in build:
            missing.append(f"build workflow missing token: {token}")
    if build and "windows-latest" in build:
        missing.append("build workflow should not rely on windows-latest for architecture coverage")
    if build and "macos-latest" in build:
        missing.append("build workflow should not rely on macos-latest for architecture coverage")


def _verify_scorecard_coverage(missing: list[str], workflow_paths: list[Path]) -> None:
    scorecard = read_workflow(
        Path(".github/workflows/ossf-scorecard.yml"),
        "ossf scorecard",
        missing,
        optional=True,
    )
    if scorecard:
        missing.extend(
            f"ossf scorecard workflow missing token: {token}"
            for token in [
                "develop",
                "main",
                "push",
                "schedule",
                "ossf-scorecard",
            ]
            if token not in scorecard
        )
        if "ossf/scorecard-action" in scorecard:
            if "github.event.repository.default_branch" not in scorecard:
                missing.append(
                    "ossf scorecard workflow must guard Scorecard execution to "
                    "the repository default branch"
                )
            if (
                "publish_results:" in scorecard
                and OSSF_DEFAULT_BRANCH_PUBLISH_GUARD not in scorecard
            ):
                missing.append(
                    "ossf scorecard publish_results must use the repository default branch guard"
                )
        for workflow_path in workflow_paths:
            workflow_content = workflow_path.read_text(encoding="utf-8")
            missing.extend(scorecard_sarif_upload_normalization_violations(workflow_content))
            missing.extend(scorecard_artifact_download_decompression_violations(workflow_content))
            missing.extend(
                ossf_scorecard_publish_restriction_violations(workflow_content, workflow_path)
            )


def verify_workflow_coverage() -> list[str]:
    """Return workflow trigger and artifact coverage violations."""
    missing: list[str] = []
    _verify_ci_coverage(missing)
    _verify_sbom_coverage(missing)
    _verify_bandit_coverage(missing)
    _verify_security_audit_coverage(missing)
    _verify_codeql_coverage(missing)
    _verify_release_coverage(missing)
    _verify_secret_scan_coverage(missing)
    _verify_build_coverage(missing)

    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for workflow_path in workflow_paths:
        workflow_content = workflow_path.read_text(encoding="utf-8")
        missing.extend(release_artifact_download_decompression_violations(workflow_content))

    _verify_scorecard_coverage(missing, workflow_paths)

    return missing


def verify_immutable_release_upload_policy() -> list[str]:
    """Return workflow violations that mutate immutable releases after publication."""
    violations: list[str] = []
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for path in workflow_paths:
        content = path.read_text(encoding="utf-8")
        if "release:" not in content or "published" not in content:
            continue
        if "gh release upload" not in content:
            continue
        violations.append(
            f"{repo_display_path(path)}: release published workflows must not "
            "upload GitHub Release assets; "
            "immutable releases require draft-before-publish asset attachment"
        )
    return violations


def verify_workflow_npx_policy() -> list[str]:
    """Return workflow npx invocations that can fetch mutable npm packages."""
    violations: list[str] = []
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for path in workflow_paths:
        for idx, line in logical_workflow_lines(path.read_text(encoding="utf-8")):
            package = npx_package_from_command(line)
            if package is None:
                continue
            violations.append(
                f"{repo_display_path(path)}:{idx} -> workflow npx package execution must use "
                f"npm exec or npx --no-install: {package}"
            )
    return violations


def verify_workflow_workspace_exec_policy() -> list[str]:
    """Return workflow npm workspace invocations that run from nested directories."""
    violations: list[str] = []
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    root_working_directories = {"", ".", "./", "${{ github.workspace }}"}

    for path in workflow_paths:
        content = path.read_text(encoding="utf-8")
        workspace_exec_lines = {
            line_number
            for line_number, logical_line in logical_workflow_lines(content)
            if WORKSPACE_EXEC_PATTERN.search(logical_line)
        }
        workflow_default_directory = ""
        current_job_default_directory = ""
        current_job_indent: int | None = None
        workflow_defaults_indent: int | None = None
        workflow_defaults_run_indent: int | None = None
        job_defaults_indent: int | None = None
        job_defaults_run_indent: int | None = None
        in_jobs = False
        step_working_directory: str | None = None
        step_uses_workspace_exec = False

        def record_step_violation(
            current_step_working_directory: str | None,
            job_default_directory: str,
            default_directory: str,
            uses_workspace_exec: bool,
            workflow_path: Path,
        ) -> None:
            effective_working_directory = (
                current_step_working_directory
                if current_step_working_directory is not None
                else job_default_directory or default_directory
            )
            if (
                uses_workspace_exec
                and effective_working_directory
                and effective_working_directory not in root_working_directories
            ):
                violations.append(
                    f"{repo_display_path(workflow_path)}: workflow npm exec --workspace commands "
                    "must run from the repository root"
                )

        lines_with_sentinel = [*content.splitlines(), "      - name: sentinel"]
        for line_number, line in enumerate(lines_with_sentinel, start=1):
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if not stripped:
                continue

            if workflow_defaults_run_indent is not None and indent <= workflow_defaults_run_indent:
                workflow_defaults_run_indent = None
            if workflow_defaults_indent is not None and indent <= workflow_defaults_indent:
                workflow_defaults_indent = None
            if job_defaults_run_indent is not None and indent <= job_defaults_run_indent:
                job_defaults_run_indent = None
            if job_defaults_indent is not None and indent <= job_defaults_indent:
                job_defaults_indent = None

            if indent == 0 and stripped == "defaults:":
                workflow_defaults_indent = indent
                workflow_defaults_run_indent = None
                continue
            if workflow_defaults_indent is not None and stripped == "run:":
                workflow_defaults_run_indent = indent
                continue
            if workflow_defaults_run_indent is not None and stripped.startswith(
                "working-directory:"
            ):
                workflow_default_directory = yaml_scalar_value(stripped)
                continue

            if indent == 0 and stripped == "jobs:":
                in_jobs = True
                continue
            if in_jobs and indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
                record_step_violation(
                    step_working_directory,
                    current_job_default_directory,
                    workflow_default_directory,
                    step_uses_workspace_exec,
                    path,
                )
                current_job_indent = indent
                current_job_default_directory = ""
                job_defaults_indent = None
                job_defaults_run_indent = None
                step_working_directory = None
                step_uses_workspace_exec = False
                continue
            if (
                current_job_indent is not None
                and indent == current_job_indent + 2
                and stripped == "defaults:"
            ):
                job_defaults_indent = indent
                job_defaults_run_indent = None
                continue
            if job_defaults_indent is not None and stripped == "run:":
                job_defaults_run_indent = indent
                continue
            if job_defaults_run_indent is not None and stripped.startswith("working-directory:"):
                current_job_default_directory = yaml_scalar_value(stripped)
                continue

            if re.match(r"^-\s+(name|uses|run):", stripped):
                record_step_violation(
                    step_working_directory,
                    current_job_default_directory,
                    workflow_default_directory,
                    step_uses_workspace_exec,
                    path,
                )
                step_working_directory = None
                step_uses_workspace_exec = False

            if stripped.startswith("working-directory:"):
                step_working_directory = yaml_scalar_value(stripped)
            if WORKSPACE_EXEC_PATTERN.search(stripped) or line_number in workspace_exec_lines:
                step_uses_workspace_exec = True

    return violations


def verify_release_asset_allowlist_policy() -> list[str]:
    """Return release workflows that upload arbitrary artifact directory contents."""
    violations: list[str] = []
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    for path in workflow_paths:
        content = path.read_text(encoding="utf-8")
        run_steps = workflow_run_steps(content)
        release_steps = [
            (index, job_content, command)
            for index, job_content, command, is_blocking in run_steps
            if command_contains_token_sequence(command, "gh release create")
        ]
        if not release_steps:
            continue
        for release_step_index, release_job_content, release_command in release_steps:
            has_generator_before_publish = any(
                job_content == release_job_content
                and index < release_step_index
                and is_blocking
                and command_contains_token_sequence(command, RELEASE_ASSET_VALIDATOR)
                for index, job_content, command, is_blocking in run_steps
            )
            release_command_lines = [line.strip() for line in shell_logical_lines(release_command)]
            revalidator_indexes = [
                line_index
                for line_index, line in enumerate(release_command_lines)
                if command_contains_token_sequence(line, RELEASE_ASSET_REVALIDATOR)
            ]
            mapfile_indexes = [
                line_index
                for line_index, line in enumerate(release_command_lines)
                if command_contains_token_sequence(line, RELEASE_ASSET_MAPFILE)
            ]
            release_create_indexes = [
                line_index
                for line_index, line in enumerate(release_command_lines)
                if command_contains_token_sequence(line, "gh release create")
            ]
            previous_release_create_index = -1
            all_release_creates_revalidated = bool(release_create_indexes)
            for release_create_index in release_create_indexes:
                has_revalidation_before_publish = any(
                    previous_release_create_index
                    < revalidator_index
                    < mapfile_index
                    < release_create_index
                    for revalidator_index in revalidator_indexes
                    for mapfile_index in mapfile_indexes
                )
                if not has_revalidation_before_publish:
                    all_release_creates_revalidated = False
                    break
                previous_release_create_index = release_create_index
            if not (has_generator_before_publish and all_release_creates_revalidated):
                violations.append(
                    f"{repo_display_path(path)}: release asset upload must use "
                    "scripts/release/select_release_assets.py"
                    " to generate and revalidate release-assets.txt"
                )
                break
        in_release_assets = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("release_assets=("):
                in_release_assets = True
            if in_release_assets and RELEASE_ARTIFACT_GLOB.search(line):
                add_release_asset_allowlist_violation(violations, path)
                break
            if in_release_assets and stripped == ")":
                in_release_assets = False

        for _, _, command, _ in run_steps:
            for line in shell_logical_lines(command):
                if not command_contains_token_sequence(line, "gh release create"):
                    continue
                if RELEASE_ARTIFACT_GLOB.search(line) or release_create_explicit_asset_tokens(line):
                    add_release_asset_allowlist_violation(violations, path)
                    break
            else:
                continue
            break
    return violations


def rust_dependency_advisory_violations(
    lockfile: Path = Path("apps/desktop/src-tauri/Cargo.lock"),
) -> list[str]:
    """Return Rust lockfile dependency versions with known required patches."""
    violations: list[str] = []
    if not lockfile.exists():
        return [f"Cargo.lock missing: {lockfile}"]
    package_dependencies = cargo_lock_package_dependencies(lockfile)
    glib_exception_owned_packages = cargo_lock_reachable_package_keys_by_name(
        package_dependencies, RUST_GLIB_LEGACY_ROOT_NAME
    )
    legacy_glib_ancestors = cargo_lock_dependency_ancestors(
        package_dependencies, RUST_GLIB_LEGACY_EXCEPTION_PACKAGE
    )
    legacy_glib_direct_owners = cargo_lock_dependency_owners(
        package_dependencies, RUST_GLIB_LEGACY_EXCEPTION_PACKAGE
    )
    for package in cargo_lock_packages(lockfile):
        current_name = str(package.get("name", ""))
        version = str(package.get("version", ""))
        if current_name == "fastrand" and version == RUST_FASTRAND_YANKED_VERSION:
            violations.append(f"{lockfile}: fastrand {version} is yanked and must stay updated")
            continue
        if current_name != "rand":
            if current_name == "glib":
                violations.extend(
                    rust_glib_advisory_violations(
                        lockfile,
                        version,
                        package_dependencies,
                        legacy_glib_ancestors,
                        legacy_glib_direct_owners,
                        glib_exception_owned_packages,
                    )
                )
            continue
        if version == RUST_RAND_RETIRED_LEGACY_VERSION:
            violations.append(
                f"{lockfile}: rand {version} is not allowed for "
                f"{RUST_RAND_ADVISORY_ID}; the former legacy owner-chain "
                "exception has been removed"
            )
            continue
        parsed_parts: list[int] = []
        segments = version.split(".")
        if any(not segment.isdecimal() for segment in segments):
            violations.append(
                f"{lockfile}: rand {version} has a non-numeric version segment "
                f"for {RUST_RAND_ADVISORY_ID}"
            )
            continue
        if len(segments) > 3:
            violations.append(
                f"{lockfile}: rand {version} has a non-standard extra version segment "
                f"for {RUST_RAND_ADVISORY_ID}"
            )
            continue
        for part in segments:
            parsed_parts.append(int(part))
        if len(parsed_parts) != len(segments):
            continue
        while len(parsed_parts) < 3:
            parsed_parts.append(0)
        parts = tuple(parsed_parts[:3])
        rand_series = (parts[0], parts[1])
        if rand_series == (0, 7):
            violations.append(
                f"{lockfile}: rand {version} is not allowed for "
                f"{RUST_RAND_ADVISORY_ID}; the former legacy owner-chain "
                "exception has been removed"
            )
            continue
        patched_version = RUST_RAND_PATCHED_VERSIONS.get(rand_series)
        if patched_version is not None and parts < patched_version:
            patched = ".".join(str(part) for part in patched_version)
            violations.append(
                f"{lockfile}: rand {version} is below patched {patched} for {RUST_RAND_ADVISORY_ID}"
            )
    return violations


def rust_audit_ignored_advisories(audit_config: Path) -> set[str]:
    """Return advisory ids tracked in cargo-audit's repo-owned ignore list."""
    if not audit_config.exists():
        return set()
    data = tomllib.loads(audit_config.read_text(encoding="utf-8"))
    advisories = data.get("advisories", {})
    if not isinstance(advisories, dict):
        return set()
    ignored = advisories.get("ignore", [])
    if not isinstance(ignored, list):
        return set()
    return {item for item in ignored if isinstance(item, str) and item}


def rust_osv_ignored_advisories(osv_config: Path) -> dict[str, str]:
    """Return advisory ids and reasons from OSV Scanner's repo-owned ignore list."""
    if not osv_config.exists():
        return {}
    data = tomllib.loads(osv_config.read_text(encoding="utf-8"))
    entries = data.get("IgnoredVulns", [])
    if not isinstance(entries, list):
        return {}
    ignored: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        advisory_id = entry.get("id")
        reason = entry.get("reason")
        if isinstance(advisory_id, str) and advisory_id:
            ignored[advisory_id] = reason if isinstance(reason, str) else ""
    return ignored


def trivy_ignored_advisories(trivy_config: Path) -> dict[str, dict[str, str]]:
    """Return advisory ids, expiry dates, and comments from Trivy's ignore file."""
    if not trivy_config.exists():
        return {}
    ignored: dict[str, dict[str, str]] = {}
    comment_buffer: list[str] = []
    for raw_line in trivy_config.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            comment_buffer = []
            continue
        if stripped.startswith("#"):
            comment_buffer.append(stripped[1:].strip())
            continue
        tokens = stripped.split()
        advisory_id = tokens[0]
        expiry = ""
        for token in tokens[1:]:
            if token.startswith("exp:"):
                expiry = token.removeprefix("exp:")
                break
        ignored[advisory_id] = {
            "expiry": expiry,
            "reason": " ".join(part for part in comment_buffer if part),
        }
        comment_buffer = []
    return ignored


def toml_decode_violation(path: Path, error: tomllib.TOMLDecodeError) -> str:
    """Return a single-line TOML decode policy violation."""
    return f"{path}: invalid TOML: {str(error).replace(chr(10), ' ')}"


def rust_osv_exception_violations(
    audit_config: Path = RUST_AUDIT_CONFIG,
    osv_config: Path = RUST_OSV_SCANNER_CONFIG,
) -> list[str]:
    """Return OSV Scanner exception drift from the cargo-audit exception scope."""
    violations: list[str] = []
    if not audit_config.exists():
        return [f"cargo audit config missing: {audit_config}"]
    if not osv_config.exists():
        return [f"OSV scanner config missing: {osv_config}"]

    try:
        audit_ignores = rust_audit_ignored_advisories(audit_config)
    except tomllib.TOMLDecodeError as error:
        violations.append(toml_decode_violation(audit_config, error))
        audit_ignores = set()
    try:
        osv_ignores = rust_osv_ignored_advisories(osv_config)
    except tomllib.TOMLDecodeError as error:
        violations.append(toml_decode_violation(osv_config, error))
        osv_ignores = {}
    if violations:
        return violations

    for advisory_id in sorted(audit_ignores - set(osv_ignores)):
        violations.append(
            f"{osv_config}: missing OSV ignore for {advisory_id} tracked in cargo audit config"
        )
    for advisory_id in sorted(set(osv_ignores) - audit_ignores):
        violations.append(
            f"{osv_config}: unexpected OSV ignore for {advisory_id} not tracked "
            "in cargo audit config"
        )
    for advisory_id, reason in sorted(osv_ignores.items()):
        if not reason.strip():
            violations.append(f"{osv_config}: OSV ignore for {advisory_id} needs a reason")
    return violations


def rust_trivy_exception_violations(
    trivy_config: Path = TRIVY_IGNORE_CONFIG,
    audit_config: Path = RUST_AUDIT_CONFIG,
    osv_config: Path = RUST_OSV_SCANNER_CONFIG,
) -> list[str]:
    """Return Trivy exception drift from repo-owned Rust advisory policy."""
    violations: list[str] = []
    try:
        audit_ignores = rust_audit_ignored_advisories(audit_config)
    except tomllib.TOMLDecodeError as error:
        return [toml_decode_violation(audit_config, error)]
    try:
        osv_ignores = rust_osv_ignored_advisories(osv_config)
    except tomllib.TOMLDecodeError as error:
        return [toml_decode_violation(osv_config, error)]

    glib_policy_active = (
        RUST_GLIB_ADVISORY_ID in audit_ignores or RUST_GLIB_ADVISORY_ID in osv_ignores
    )
    if not trivy_config.exists():
        return [f"Trivy ignore config missing: {trivy_config}"]

    trivy_ignores = trivy_ignored_advisories(trivy_config)
    entry = trivy_ignores.get(RUST_GLIB_TRIVY_ADVISORY_ID)

    if glib_policy_active and entry is None:
        violations.append(
            f"{trivy_config}: missing Trivy ignore for {RUST_GLIB_TRIVY_ADVISORY_ID} "
            f"tracked as {RUST_GLIB_ADVISORY_ID}"
        )
        return violations
    if not glib_policy_active and RUST_GLIB_TRIVY_ADVISORY_ID in trivy_ignores:
        violations.append(
            f"{trivy_config}: unexpected Trivy ignore for "
            f"{RUST_GLIB_TRIVY_ADVISORY_ID} without matching cargo-audit/OSV policy"
        )
        return violations
    if not glib_policy_active:
        return violations

    reason = entry["reason"]
    required_reason_tokens = (
        RUST_GLIB_ADVISORY_ID,
        "glib 0.18.5",
        "glib >=0.20",
        "Tauri/wry/webkit2gtk/gtk GTK3 stack",
        "Windows/macOS artifacts only",
        "verify_supply_chain.py",
        "remove when upstream drops or patches the chain",
    )
    for token in required_reason_tokens:
        if token not in reason:
            violations.append(
                f"{trivy_config}: Trivy ignore for {RUST_GLIB_TRIVY_ADVISORY_ID} "
                f"must document {token}"
            )

    expiry = entry["expiry"]
    if not expiry:
        violations.append(
            f"{trivy_config}: Trivy ignore for {RUST_GLIB_TRIVY_ADVISORY_ID} "
            "must include an exp:YYYY-MM-DD revisit date"
        )
        return violations
    try:
        expiry_date = date.fromisoformat(expiry)
    except ValueError:
        violations.append(
            f"{trivy_config}: Trivy ignore for {RUST_GLIB_TRIVY_ADVISORY_ID} "
            f"has invalid exp date {expiry}"
        )
        return violations
    if expiry_date <= date.today():
        violations.append(
            f"{trivy_config}: Trivy ignore for {RUST_GLIB_TRIVY_ADVISORY_ID} "
            f"expired on {expiry}; re-check upstream and remove or renew with evidence"
        )
    return violations


def rust_glib_advisory_violations(
    lockfile: Path,
    version: str,
    package_dependencies: dict[str, list[str]],
    legacy_glib_ancestors: set[str],
    legacy_glib_direct_owners: set[str],
    glib_exception_owned_packages: set[str],
) -> list[str]:
    """Return violations for vulnerable glib versions outside the Tauri GTK stack."""
    if version == RUST_GLIB_LEGACY_EXCEPTION_VERSION:
        if glib_legacy_exception_owners_are_allowed(
            package_dependencies,
            legacy_glib_ancestors,
            glib_exception_owned_packages,
            legacy_glib_direct_owners,
        ):
            return []
        return [
            f"{lockfile}: glib {version} matches the legacy exception version but "
            "does not have the documented Tauri/wry/webkit2gtk/gtk owner chain "
            f"for {RUST_GLIB_ADVISORY_ID}"
        ]

    version_violation = unsupported_numeric_semver_violation(
        lockfile, "glib", version, RUST_GLIB_ADVISORY_ID
    )
    if version_violation is not None:
        return [version_violation]
    parsed_version = parse_numeric_semver(version)
    if parsed_version is None:  # Defensive; unsupported forms returned above.
        return [
            f"{lockfile}: glib {version} has an unsupported version form "
            f"for {RUST_GLIB_ADVISORY_ID}"
        ]
    if parsed_version < RUST_GLIB_PATCHED_VERSION:
        patched = ".".join(str(part) for part in RUST_GLIB_PATCHED_VERSION)
        return [
            f"{lockfile}: glib {version} is below patched {patched} for {RUST_GLIB_ADVISORY_ID}"
        ]
    return []


def glib_legacy_exception_owners_are_allowed(
    package_dependencies: dict[str, list[str]],
    legacy_glib_ancestors: set[str],
    glib_exception_owned_packages: set[str],
    legacy_glib_direct_owners: set[str],
) -> bool:
    """Return whether every glib ancestor matches the documented GTK/WebKit stack."""
    if not legacy_glib_ancestors:
        return False
    ancestor_names = {ancestor.rsplit(" ", maxsplit=1)[0] for ancestor in legacy_glib_ancestors}
    direct_owner_names = {owner.rsplit(" ", maxsplit=1)[0] for owner in legacy_glib_direct_owners}
    if not direct_owner_names <= RUST_GLIB_LEGACY_DIRECT_OWNER_NAMES:
        return False
    off_chain_ancestors = legacy_glib_ancestors - glib_exception_owned_packages
    allowed_app_roots = {
        ancestor
        for ancestor in off_chain_ancestors
        if ancestor.rsplit(" ", maxsplit=1)[0] in RUST_GLIB_LEGACY_ALLOWED_APP_ROOT_NAMES
    }
    if off_chain_ancestors != allowed_app_roots:
        return False
    if not glib_allowed_app_roots_reach_glib_through_tauri(package_dependencies, allowed_app_roots):
        return False
    return ancestor_names <= (
        RUST_GLIB_LEGACY_ALLOWED_ANCESTOR_NAMES | RUST_GLIB_LEGACY_ALLOWED_APP_ROOT_NAMES
    )


def glib_allowed_app_roots_reach_glib_through_tauri(
    package_dependencies: dict[str, list[str]], allowed_app_roots: set[str]
) -> bool:
    """Return whether app roots reach legacy glib only through Tauri."""
    for app_root in allowed_app_roots:
        glib_reaching_dependencies = {
            dependency
            for dependency in package_dependencies.get(app_root, [])
            if RUST_GLIB_LEGACY_EXCEPTION_PACKAGE
            in cargo_lock_reachable_package_keys(package_dependencies, dependency)
        }
        glib_reaching_dependency_names = {
            dependency.rsplit(" ", maxsplit=1)[0] for dependency in glib_reaching_dependencies
        }
        if glib_reaching_dependency_names != {RUST_GLIB_LEGACY_ROOT_NAME}:
            return False
        if not cargo_lock_has_named_dependency_path(
            package_dependencies, app_root, RUST_GLIB_LEGACY_EXPECTED_CHAIN_NAMES
        ):
            return False
    return True


def cargo_lock_has_named_dependency_path(
    package_dependencies: dict[str, list[str]],
    root_package: str,
    package_names: tuple[str, ...],
) -> bool:
    """Return whether a dependency path contains package names in order."""
    pending: list[tuple[str, int, frozenset[str]]] = [(root_package, 0, frozenset())]
    while pending:
        current, matched_count, seen = pending.pop()
        if current in seen:
            continue
        current_name = current.rsplit(" ", maxsplit=1)[0]
        next_matched_count = matched_count
        if matched_count < len(package_names) and current_name == package_names[matched_count]:
            next_matched_count += 1
            if next_matched_count == len(package_names):
                return True
        next_seen = seen | {current}
        for dependency in package_dependencies.get(current, []):
            pending.append((dependency, next_matched_count, next_seen))
    return False


def unsupported_numeric_semver_violation(
    lockfile: Path, package_name: str, version: str, advisory_id: str
) -> str | None:
    """Return a violation for non-numeric or overly long Cargo version forms."""
    segments = version.split(".")
    if any(not segment.isdecimal() for segment in segments):
        return (
            f"{lockfile}: {package_name} {version} has a non-numeric version segment "
            f"for {advisory_id}"
        )
    if len(segments) > 3:
        return (
            f"{lockfile}: {package_name} {version} has a non-standard extra version segment "
            f"for {advisory_id}"
        )
    return None


def parse_numeric_semver(version: str) -> tuple[int, int, int] | None:
    """Return a three-part numeric semver tuple for supported Cargo versions."""
    segments = version.split(".")
    if any(not segment.isdecimal() for segment in segments):
        return None
    if len(segments) > 3:
        return None
    parsed_parts = [int(part) for part in segments]
    while len(parsed_parts) < 3:
        parsed_parts.append(0)
    return parsed_parts[0], parsed_parts[1], parsed_parts[2]


@functools.lru_cache
def cargo_lock_package_dependencies(lockfile: Path) -> dict[str, list[str]]:
    """Return Cargo package keys and dependency tokens from a lockfile."""
    packages: dict[str, list[str]] = {}
    for package in cargo_lock_packages(lockfile):
        current_name = str(package.get("name", ""))
        current_version = str(package.get("version", ""))
        if not current_name or not current_version:
            continue
        dependencies = package.get("dependencies", [])
        if not isinstance(dependencies, list):
            dependencies = []
        packages[f"{current_name} {current_version}"] = [
            str(dependency).strip() for dependency in dependencies
        ]
    return cargo_lock_normalized_package_dependencies(packages)


@functools.lru_cache
def cargo_lock_packages(lockfile: Path) -> list[dict[str, object]]:
    """Return Cargo package tables from supported lockfile TOML forms."""
    packages: list[dict[str, object]] = []
    current_package: dict[str, object] | None = None
    in_dependencies = False
    dependency_tokens: list[str] = []

    def store_current_package() -> None:
        if current_package is not None:
            if in_dependencies:
                current_package["dependencies"] = dependency_tokens.copy()
            packages.append(current_package.copy())

    for line in [*lockfile.read_text(encoding="utf-8").splitlines(), "[[package]]"]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "[[package]]":
            store_current_package()
            current_package = {}
            in_dependencies = False
            dependency_tokens = []
            continue
        if current_package is None:
            continue
        if in_dependencies:
            if stripped == "]":
                current_package["dependencies"] = dependency_tokens.copy()
                in_dependencies = False
                continue
            if stripped.startswith('"'):
                dependency_tokens.append(stripped.strip('",'))
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key == "dependencies":
            if normalized_value == "[":
                in_dependencies = True
                dependency_tokens = []
                continue
            current_package["dependencies"] = parse_cargo_lock_string_list(normalized_value)
            continue
        if normalized_key in {"name", "version"}:
            current_package[normalized_key] = parse_cargo_lock_scalar(normalized_value)
    return packages


def parse_cargo_lock_string_list(value: str) -> list[str]:
    """Return strings from an inline Cargo.lock dependency array."""
    parsed_value = parse_cargo_lock_toml_value(value)
    if not isinstance(parsed_value, list):
        return []
    return [str(item).strip() for item in parsed_value]


def parse_cargo_lock_scalar(value: str) -> str:
    """Return a scalar Cargo.lock TOML value as text."""
    parsed_value = parse_cargo_lock_toml_value(value)
    if parsed_value is None:
        return ""
    return str(parsed_value)


def parse_cargo_lock_toml_value(value: str) -> object | None:
    """Return a TOML value from Cargo.lock, or None when parsing fails."""
    try:
        return tomllib.loads(f"v = {value}")["v"]
    except tomllib.TOMLDecodeError:
        return None


def cargo_lock_normalized_package_dependencies(
    package_dependencies: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Return dependency tokens normalized to exact package keys when possible."""
    package_keys_by_name: dict[str, list[str]] = {}
    for package_key in package_dependencies:
        package_name = package_key.rsplit(" ", maxsplit=1)[0]
        package_keys_by_name.setdefault(package_name, []).append(package_key)

    normalized: dict[str, list[str]] = {}
    for package_key, dependency_tokens in package_dependencies.items():
        normalized_tokens: list[str] = []
        for dependency_token in dependency_tokens:
            dependency = dependency_token.strip()
            if dependency in package_dependencies:
                normalized_tokens.append(dependency)
                continue
            matching_package_keys = package_keys_by_name.get(dependency, [])
            if len(matching_package_keys) == 1:
                normalized_tokens.append(matching_package_keys[0])
                continue
            normalized_tokens.append(dependency)
        normalized[package_key] = normalized_tokens
    return normalized


def cargo_lock_dependency_owners(
    package_dependencies: dict[str, list[str]], dependency: str
) -> set[str]:
    """Return package keys that directly reference the target dependency key."""
    return {
        owner
        for owner, dependency_tokens in package_dependencies.items()
        if dependency in dependency_tokens
    }


def cargo_lock_dependency_ancestors(
    package_dependencies: dict[str, list[str]], dependency: str
) -> set[str]:
    """Return every package key that can reach the target dependency key."""
    reverse_dependencies: dict[str, set[str]] = {}
    for package_key, dependency_tokens in package_dependencies.items():
        for dependency_token in dependency_tokens:
            reverse_dependencies.setdefault(dependency_token, set()).add(package_key)

    ancestors: set[str] = set()
    pending = list(reverse_dependencies.get(dependency, set()))
    while pending:
        current = pending.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        pending.extend(reverse_dependencies.get(current, set()))
    return ancestors


def cargo_lock_reachable_package_keys(
    package_dependencies: dict[str, list[str]], root_package: str
) -> set[str]:
    """Return package keys reachable from a root package dependency graph."""
    reachable: set[str] = set()
    pending = [root_package]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(package_dependencies.get(current, []))
    return reachable


def cargo_lock_reachable_package_keys_by_name(
    package_dependencies: dict[str, list[str]], root_package_name: str
) -> set[str]:
    """Return packages reachable from every package whose key has the root name."""
    reachable: set[str] = set()
    for package_key in package_dependencies:
        package_name = package_key.rsplit(" ", maxsplit=1)[0]
        if package_name == root_package_name:
            reachable.update(cargo_lock_reachable_package_keys(package_dependencies, package_key))
    return reachable


def cargo_lock_has_dependency_chain(
    package_dependencies: dict[str, list[str]], package_chain: tuple[str, ...]
) -> bool:
    """Return whether Cargo dependencies contain the exact package chain."""
    return all(
        cargo_dependency_targets_package(package_dependencies, owner, dependency)
        for owner, dependency in pairwise(package_chain)
    )


def cargo_dependency_targets_package(
    package_dependencies: dict[str, list[str]], owner: str, dependency: str
) -> bool:
    """Return whether an owner package depends on the target package key."""
    dependency_tokens = package_dependencies.get(owner, [])
    return dependency in dependency_tokens


def main() -> int:
    """Return a failing exit code when supply-chain controls are incomplete."""
    violations: list[str] = []
    violations.extend(f"missing file: {item}" for item in verify_required_files())
    violations.extend(verify_pinned_actions())
    violations.extend(verify_checkout_default_branch_guard())
    violations.extend(verify_dependabot_coverage())
    violations.extend(verify_workflow_coverage())
    violations.extend(verify_immutable_release_upload_policy())
    violations.extend(verify_release_asset_allowlist_policy())
    violations.extend(verify_workflow_npx_policy())
    violations.extend(verify_workflow_workspace_exec_policy())
    violations.extend(rust_osv_exception_violations())
    violations.extend(rust_trivy_exception_violations())
    violations.extend(rust_dependency_advisory_violations())

    if violations:
        print("Supply-chain verification failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Supply-chain verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
