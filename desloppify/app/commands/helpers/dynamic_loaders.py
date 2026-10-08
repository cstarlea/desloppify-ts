"""Approved dynamic loader seams for app-layer command modules."""

from __future__ import annotations

import importlib
from types import ModuleType


def load_score_update_module() -> ModuleType:
    """Load the queue/score update helper on demand."""
    return importlib.import_module("desloppify.app.commands.helpers.score_update")


def load_optional_scorecard_module() -> ModuleType | None:
    """Load the optional scorecard module when PIL-backed output is available."""
    try:
        return importlib.import_module("desloppify.app.output.scorecard")
    except ImportError:
        return None


__all__ = [
    "load_optional_scorecard_module",
    "load_score_update_module",
]
