"""Run the project's own oxlint and read its JSON output.

Rule codes are renamed to ESLint's (``typescript(no-explicit-any)`` is
``@typescript-eslint/no-explicit-any``), so the dedup and confidence tables
apply, and a rule that both oxlint and ESLint report on a line is one issue.
Each rule's category (``oxlint --rules``) stands in for ESLint's ``meta.type``.
oxlint doesn't list the files it linted, so the run counts the scan's files.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.lint.configs import LinterConfig
from desloppify.languages.typescript.detectors.lint.runner import (
    LinterRun,
    LintMessage,
    run_linter,
    run_process,
)

_CODE_RE = re.compile(r"^([\w@/-]+)\((.+)\)$")
_SCOPES = {
    "eslint": "",
    "typescript": "@typescript-eslint/",
    "typescript-eslint": "@typescript-eslint/",
    "jsx_a11y": "jsx-a11y/",
    "react_perf": "react-perf/",
    "nextjs": "@next/next/",
}
_CATEGORY_META: dict[str, dict[str, Any]] = {
    "correctness": {"type": "problem"},
    "suspicious": {"type": "problem"},
    "pedantic": {"type": "suggestion"},
    "perf": {"type": "suggestion"},
    "restriction": {"type": "suggestion"},
    "style": {"type": "suggestion", "docs": {"recommended": "stylistic"}},
    "nursery": {"type": "suggestion", "docs": {"recommended": "stylistic"}},
}


def rule_name(scope: str, rule: str) -> str:
    scope = scope.removeprefix("eslint-plugin-")
    return _SCOPES.get(scope, f"{scope}/") + rule


def _rule_meta(binary: Path, config: LinterConfig) -> dict[str, dict[str, Any]]:
    try:
        result = run_process([str(binary), "--rules", "--format", "json"], config, 60)
        rules = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}
    meta: dict[str, dict[str, Any]] = {}
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("category") in _CATEGORY_META:
            meta[rule_name(str(rule.get("scope")), str(rule.get("value")))] = (
                _CATEGORY_META[rule["category"]]
            )
    return meta


def parse_oxlint_output(
    stdout: str, run: LinterRun, files: set[Path], meta: dict[str, dict[str, Any]]
) -> bool:
    """Fill ``run`` from ``--format json`` output; False if it isn't that."""
    start = stdout.find("{")
    try:
        data = json.loads(stdout[start:]) if start >= 0 else None
    except ValueError:
        return False
    if not isinstance(data, dict) or not isinstance(data.get("diagnostics"), list):
        return False
    for diagnostic in data["diagnostics"]:
        if not isinstance(diagnostic, dict) or not diagnostic.get("filename"):
            continue
        file = (run.config.directory / str(diagnostic["filename"])).resolve()
        match = _CODE_RE.match(str(diagnostic.get("code") or ""))
        if match is None:
            run.unparsed.add(file)  # parse errors carry no rule code
            continue
        rule = rule_name(*match.groups())
        labels = diagnostic.get("labels")
        span = (
            labels[0].get("span")
            if isinstance(labels, list) and labels and isinstance(labels[0], dict)
            else None
        )
        span = span if isinstance(span, dict) else {}
        run.messages.append(
            LintMessage(
                file=file,
                line=int(span.get("line") or 0),
                col=int(span.get("column") or 0),
                rule=rule,
                severity="error"
                if diagnostic.get("severity") == "error"
                else "warning",
                message=str(diagnostic.get("message") or ""),
                fixable=False,
                meta=meta.get(rule),
            )
        )
    run.files = set(files) if data.get("number_of_files") != 0 else set()
    return True


def run_oxlint(
    binary: Path,
    config: LinterConfig,
    targets: list[str],
    ignore_patterns: list[str],
    files: set[Path],
) -> LinterRun:
    """``oxlint`` from the config's directory on ``targets``; ``files`` are the scan's."""
    meta = _rule_meta(binary, config)
    cmd = [str(binary), "--format", "json"]
    for pattern in ignore_patterns:
        cmd.append(f"--ignore-pattern={pattern}")
    return run_linter(
        [*cmd, *targets],
        LinterRun(config=config),
        lambda stdout, run: parse_oxlint_output(stdout, run, files, meta),
    )


__all__ = ["parse_oxlint_output", "rule_name", "run_oxlint"]
