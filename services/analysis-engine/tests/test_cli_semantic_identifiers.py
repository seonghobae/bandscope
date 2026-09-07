"""Naming contracts for the CLI job-input authority boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from bandscope_analysis import cli


def test_cli_job_input_boundary_uses_semantic_identifiers() -> None:
    """Keep ambiguous one-word names out of the owned job-input implementation."""
    source_path = Path(cli.__file__)
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    prohibited_names = {
        "authority",
        "before",
        "components",
        "descriptor",
        "drive",
        "flags",
        "opened",
        "path",
        "payload",
        "request",
        "response",
        "stream",
        "token",
        "update",
    }
    observed_names: set[str] = set()

    for source_node in ast.walk(source_tree):
        if isinstance(source_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            observed_names.update(argument.arg for argument in source_node.args.args)
        elif isinstance(source_node, ast.Name) and isinstance(source_node.ctx, ast.Store):
            observed_names.add(source_node.id)

    assert prohibited_names.isdisjoint(observed_names)


def test_cli_does_not_publish_retired_temporal_probe_hook() -> None:
    """The CLI must not expose a no-op hook for analysis now owned by the API."""
    assert not hasattr(cli, "TemporalAnalyzer")
