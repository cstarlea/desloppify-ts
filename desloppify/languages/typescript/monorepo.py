"""Monorepo mode: per-package tsc and lint runs for a scan of a monorepo's root.

Off by default. ``languages.typescript.monorepo_mode`` turns it on in config
and ``--lang-opt monorepo_mode=packages`` for one scan (``off`` turns it off).
"""

from __future__ import annotations

from desloppify.base.output.terminal import log
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.languages.typescript.detectors.bounded import Budget

MODES = ("off", "packages")
DEFAULT_BUDGET_SECONDS = 600
DEFAULT_MAX_MEMORY_MB = 3072


def monorepo_mode(lang: LangRuntimeContract) -> str:
    """The scan's mode: the ``--lang-opt`` value, else the config setting."""
    raw = lang.runtime_option("monorepo_mode", "") or lang.runtime_setting(
        "monorepo_mode", "off"
    )
    mode = str(raw or "off").strip().lower()
    if mode not in MODES:
        log(
            f"         unknown monorepo_mode {mode!r} (expected {' or '.join(MODES)}); using off"
        )
        return "off"
    return mode


def monorepo_budget(lang: LangRuntimeContract) -> Budget | None:
    """A fresh budget for one detector's package runs, or None when the mode is off."""
    if monorepo_mode(lang) == "off":
        return None
    seconds = lang.runtime_setting("monorepo_budget_seconds", DEFAULT_BUDGET_SECONDS)
    memory = lang.runtime_setting("monorepo_max_memory_mb", DEFAULT_MAX_MEMORY_MB)
    return Budget(max(0, int(seconds)), max(256, int(memory)))


__all__ = [
    "DEFAULT_BUDGET_SECONDS",
    "DEFAULT_MAX_MEMORY_MB",
    "MODES",
    "monorepo_budget",
    "monorepo_mode",
]
