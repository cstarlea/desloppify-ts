"""Run the project's own ESLint and read its JSON output."""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.lint.configs import LinterConfig, find_local_bin

logger = logging.getLogger(__name__)
_proc_runtime = subprocess

LINT_TIMEOUT = 300
PRINT_CONFIG_TIMEOUT = 60
# parserOptions that make typescript-eslint build a TypeScript program.
_TYPE_INFO_OPTIONS = ("project", "projectService", "EXPERIMENTAL_useProjectService", "programs")
_UNUSED_DIRECTIVE = "unused-disable-directive"


@dataclass(frozen=True)
class LintMessage:
    file: Path
    line: int
    col: int
    rule: str
    severity: str  # "error" | "warning"
    message: str
    fixable: bool
    meta: dict[str, Any] | None


@dataclass
class LinterRun:
    """One linter run; ``failure`` says why it produced nothing usable, if it didn't."""

    config: LinterConfig
    failure: str | None = None
    error: str = ""
    messages: list[LintMessage] = field(default_factory=list)
    files: set[Path] = field(default_factory=set)
    unparsed: set[Path] = field(default_factory=set)


def _env(config: LinterConfig) -> dict[str, str]:
    env = dict(os.environ)
    # ESLint 9 reads .eslintrc only with this set; 8 reads it by default.
    env["ESLINT_USE_FLAT_CONFIG"] = "false" if config.legacy else "true"
    return env


def _error_summary(stderr: str) -> str:
    """The first informative stderr line of a crashed ESLint."""
    for line in stderr.splitlines():
        text = line.strip()
        if text and not text.startswith(("Oops! Something went wrong", "ESLint: ", "(node:")):
            return text[:300]
    return ""


def _failure(result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    stderr = result.stderr or ""
    if "heap out of memory" in stderr:
        return "linter_oom", "ESLint ran out of memory"
    return "linter_failed", _error_summary(stderr) or f"exit code {result.returncode}"


def _run(cmd: list[str], config: LinterConfig, timeout: int) -> subprocess.CompletedProcess[str]:
    return _proc_runtime.run(  # nosec B603
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=config.directory,
        env=_env(config),
        timeout=timeout,
    )


def eslint_binary(config: LinterConfig) -> Path | None:
    return find_local_bin("eslint", config.directory)


def uses_type_information(
    binary: Path, config: LinterConfig, sample: Path
) -> tuple[bool, str | None, str]:
    """Whether ``sample``'s resolved config builds a TypeScript program.

    Returns ``(type_aware, failure, error)``. ``--print-config`` loads the
    config and its plugins, so a broken config fails here, before the run.
    """
    try:
        result = _run([str(binary), "--print-config", str(sample)], config, PRINT_CONFIG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "linter_timeout", f"ESLint --print-config took over {PRINT_CONFIG_TIMEOUT}s"
    except OSError as exc:
        return False, "linter_missing", str(exc)
    if result.returncode != 0:
        failure, error = _failure(result)
        return False, failure, error
    try:
        resolved = json.loads(result.stdout)
    except ValueError:
        resolved = None  # "undefined" for a file the config ignores
    if not isinstance(resolved, dict):
        return False, None, ""
    language_options = resolved.get("languageOptions")
    options = (
        language_options.get("parserOptions") if isinstance(language_options, dict) else None
    ) or resolved.get("parserOptions")
    if not isinstance(options, dict):
        return False, None, ""
    return any(options.get(key) for key in _TYPE_INFO_OPTIONS), None, ""


def _parse_messages(file: Path, raw: list[Any], rules_meta: dict[str, Any], run: LinterRun) -> None:
    for message in raw:
        if not isinstance(message, dict):
            continue
        rule = message.get("ruleId")
        text = str(message.get("message") or "")
        if message.get("fatal"):
            run.unparsed.add(file)
            continue
        if not rule:
            if not text.startswith("Unused eslint-disable directive"):
                continue  # "File ignored ..." and similar notices
            rule = _UNUSED_DIRECTIVE
        run.messages.append(
            LintMessage(
                file=file,
                line=int(message.get("line") or 0),
                col=int(message.get("column") or 0),
                rule=str(rule),
                severity="error" if message.get("severity") == 2 else "warning",
                message=text,
                fixable="fix" in message,
                meta=rules_meta.get(rule) if isinstance(rules_meta.get(rule), dict) else None,
            )
        )


def parse_eslint_output(stdout: str, config: LinterConfig, run: LinterRun) -> bool:
    """Fill ``run`` from ``json`` or ``json-with-metadata`` output; False if it isn't that."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return False
    rules_meta: dict[str, Any] = {}
    if isinstance(data, dict):
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("rulesMeta"), dict):
            rules_meta = metadata["rulesMeta"]
        data = data.get("results")
    if not isinstance(data, list):
        return False
    for result in data:
        if not isinstance(result, dict) or not result.get("filePath"):
            continue
        file = Path(str(result["filePath"]))
        if not file.is_absolute():
            file = config.directory / file
        file = file.resolve()
        run.files.add(file)
        messages = result.get("messages")
        if isinstance(messages, list):
            _parse_messages(file, messages, rules_meta, run)
    return True


def run_eslint(
    binary: Path, config: LinterConfig, targets: list[str], ignore_patterns: list[str]
) -> LinterRun:
    """Run ESLint from the config's directory on ``targets`` (relative to it)."""
    run = LinterRun(config=config)
    cmd = [str(binary), "--format", "json-with-metadata", "--no-error-on-unmatched-pattern"]
    for pattern in ignore_patterns:
        cmd += ["--ignore-pattern", pattern]
    try:
        result = _run([*cmd, *targets], config, LINT_TIMEOUT)
    except subprocess.TimeoutExpired:
        run.failure, run.error = "linter_timeout", f"ESLint took over {LINT_TIMEOUT}s"
        return run
    except OSError as exc:
        run.failure, run.error = "linter_missing", str(exc)
        return run
    if result.returncode not in (0, 1) or not parse_eslint_output(result.stdout, config, run):
        run.failure, run.error = _failure(result)
        logger.debug("ESLint failed (%s): %s", run.failure, (result.stderr or "")[-500:])
    return run


__all__ = [
    "LINT_TIMEOUT",
    "LintMessage",
    "LinterRun",
    "eslint_binary",
    "parse_eslint_output",
    "run_eslint",
    "uses_type_information",
]
