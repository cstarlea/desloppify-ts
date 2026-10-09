"""What every linter run shares: the process call, failures and the parsed messages."""

from __future__ import annotations

import contextlib
import contextvars
import os
import subprocess  # nosec B404
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.bounded import (
    MemoryLimitExceeded,
    RunLimits,
    run_bounded,
)
from desloppify.languages.typescript.detectors.lint.configs import LinterConfig

_proc_runtime = subprocess

LINT_TIMEOUT = 300
# Set while monorepo mode lints a package: its runs go through run_bounded.
_LIMITS: contextvars.ContextVar[RunLimits | None] = contextvars.ContextVar(
    "lint_limits", default=None
)


@contextlib.contextmanager
def bounded_runs(limits: RunLimits) -> Iterator[None]:
    """Run the linter processes started inside the block under ``limits``."""
    token = _LIMITS.set(limits)
    try:
        yield
    finally:
        _LIMITS.reset(token)


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
    if config.linter == "eslint":
        # ESLint 9 reads .eslintrc only with this set; 8 reads it by default.
        env["ESLINT_USE_FLAT_CONFIG"] = "false" if config.legacy else "true"
    return env


def run_process(
    cmd: list[str], config: LinterConfig, timeout: int = LINT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` from the config's directory."""
    limits = _LIMITS.get()
    if limits is not None:
        bounded = RunLimits(min(timeout, limits.timeout), limits.max_memory_mb)
        return run_bounded(cmd, cwd=config.directory, limits=bounded, env=_env(config))
    return _proc_runtime.run(  # nosec B603
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        cwd=config.directory,
        env=_env(config),
        timeout=timeout,
    )


def _error_summary(stderr: str) -> str:
    """The first informative stderr line of a crashed linter."""
    for line in stderr.splitlines():
        text = line.strip(" ×✖\t")
        if text and not text.startswith(
            ("Oops! Something went wrong", "ESLint: ", "(node:")
        ):
            return text[:300]
    return ""


def failure_of(
    result: subprocess.CompletedProcess[str], config: LinterConfig
) -> tuple[str, str]:
    stderr = result.stderr or ""
    if "heap out of memory" in stderr:
        return "linter_oom", f"{config.label} ran out of memory"
    return "linter_failed", _error_summary(stderr) or f"exit code {result.returncode}"


def run_linter(
    cmd: list[str], run: LinterRun, parse: Any, ok_codes: tuple[int, ...] = (0, 1)
) -> LinterRun:
    """Run ``cmd`` and fill ``run`` with ``parse(stdout, run)``; a failure is recorded on ``run``."""
    label = run.config.label
    try:
        result = run_process(cmd, run.config)
    except subprocess.TimeoutExpired as exc:
        run.failure, run.error = (
            "linter_timeout",
            f"{label} took over {exc.timeout:.0f}s",
        )
        return run
    except MemoryLimitExceeded as exc:
        run.failure, run.error = "linter_oom", f"{label} went {exc}"
        return run
    except OSError as exc:
        run.failure, run.error = "linter_missing", str(exc)
        return run
    if result.returncode not in ok_codes or not parse(result.stdout, run):
        run.failure, error = failure_of(result, run.config)
        run.error = run.error or error
    return run


__all__ = [
    "LINT_TIMEOUT",
    "LintMessage",
    "LinterRun",
    "bounded_runs",
    "failure_of",
    "run_linter",
    "run_process",
]
