"""Run the project's own ESLint (or XO, which wraps it) and read its JSON output."""

from __future__ import annotations

import json
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.bounded import MemoryLimitExceeded
from desloppify.languages.typescript.detectors.lint.configs import LinterConfig
from desloppify.languages.typescript.detectors.lint.runner import (
    LinterRun,
    LintMessage,
    failure_of,
    run_linter,
    run_process,
)

PRINT_CONFIG_TIMEOUT = 60
# parserOptions that make typescript-eslint build a TypeScript program.
_TYPE_INFO_OPTIONS = ("project", "projectService", "EXPERIMENTAL_useProjectService", "programs")
_UNUSED_DIRECTIVE = "unused-disable-directive"


def uses_type_information(
    binary: Path, config: LinterConfig, sample: Path
) -> tuple[bool, str | None, str]:
    """Whether ``sample``'s resolved config builds a TypeScript program.

    Returns ``(type_aware, failure, error)``. ``--print-config`` loads the
    config and its plugins, so a broken config fails here, before the run.
    """
    flag = ["--print-config", str(sample)] if config.linter == "eslint" else [f"--print-config={sample}"]
    try:
        result = run_process([str(binary), *flag], config, PRINT_CONFIG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "linter_timeout", f"--print-config took over {PRINT_CONFIG_TIMEOUT}s"
    except MemoryLimitExceeded as exc:
        return False, "linter_oom", f"--print-config went {exc}"
    except OSError as exc:
        return False, "linter_missing", str(exc)
    if result.returncode != 0:
        failure, error = failure_of(result, config)
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


def parse_eslint_output(stdout: str, run: LinterRun) -> bool:
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
            file = run.config.directory / file
        file = file.resolve()
        run.files.add(file)
        messages = result.get("messages")
        if isinstance(messages, list):
            _parse_messages(file, messages, rules_meta, run)
    return True


def run_eslint(
    binary: Path, config: LinterConfig, targets: list[str], ignore_patterns: list[str]
) -> LinterRun:
    """Run ESLint or XO from the config's directory on ``targets`` (relative to it).

    XO's own json-with-metadata reporter crashes (it reads metadata from a
    different ESLint instance), so XO gets plain ``json`` and no rule metadata.
    """
    if config.linter == "xo":
        cmd = [str(binary), "--reporter=json"]
        for pattern in ignore_patterns:
            cmd.append(f"--ignore={pattern}")
    else:
        cmd = [str(binary), "--format", "json-with-metadata", "--no-error-on-unmatched-pattern"]
        for pattern in ignore_patterns:
            cmd += ["--ignore-pattern", pattern]
    return run_linter([*cmd, *targets], LinterRun(config=config), parse_eslint_output)


__all__ = ["parse_eslint_output", "run_eslint", "uses_type_information"]
