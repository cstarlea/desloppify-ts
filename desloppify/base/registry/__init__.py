"""Canonical detector registry — single source of truth.

All detector metadata lives here. Other modules derive their views
(display order, CLI names, narrative tools, scoring validation) from this registry
instead of maintaining their own lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from types import MappingProxyType
from typing import Any

from .catalog_entries import DETECTORS as _CATALOG_DETECTORS
from .catalog_models import (
    DISPLAY_ORDER as _CATALOG_DISPLAY_ORDER,
)
from .catalog_models import (
    DetectorMeta,
)

DETECTORS: Mapping[str, DetectorMeta] = MappingProxyType(dict(_CATALOG_DETECTORS))
_DISPLAY_ORDER: Sequence[str] = tuple(_CATALOG_DISPLAY_ORDER)
JUDGMENT_DETECTORS: Set[str] = frozenset(
    name for name, meta in DETECTORS.items() if meta.needs_judgment
)


def detector_names() -> list[str]:
    """All registered detector names, sorted."""
    return sorted(DETECTORS)


def get_detector_meta(name: str) -> DetectorMeta | None:
    """Lookup one detector metadata entry by name."""
    return DETECTORS.get(name)


def display_order() -> list[str]:
    """Canonical display order for terminal output."""
    return list(_DISPLAY_ORDER)


_ACTION_PRIORITY = {"auto_fix": 0, "reorganize": 1, "refactor": 2, "manual_fix": 3}
_ACTION_LABELS = {
    "auto_fix": "autofix",
    "reorganize": "move",
    "refactor": "refactor",
    "manual_fix": "manual",
}


def dimension_action_type(dim_name: str) -> str:
    """Return a compact action type label for a dimension based on its detectors."""
    best = "manual"
    best_priority = 99
    for detector_meta in DETECTORS.values():
        if detector_meta.dimension == dim_name:
            priority = _ACTION_PRIORITY.get(detector_meta.action_type, 99)
            if priority < best_priority:
                best_priority = priority
                best = detector_meta.action_type
    return _ACTION_LABELS.get(best, "manual")


def detector_tools() -> dict[str, dict[str, Any]]:
    """Build detector tool metadata keyed by detector name."""
    result = {}
    for detector_name, detector_meta in DETECTORS.items():
        entry: dict[str, Any] = {
            "fixers": list(detector_meta.fixers),
            "action_type": detector_meta.action_type,
        }
        if detector_meta.tool:
            entry["tool"] = detector_meta.tool
        if detector_meta.guidance:
            entry["guidance"] = detector_meta.guidance
        result[detector_name] = entry
    return result


def dimension_to_detectors() -> dict[str, set[str]]:
    """Subjective dimension -> set of detector names that provide evidence."""
    result: dict[str, set[str]] = {}
    for name, meta in DETECTORS.items():
        for dim in meta.subjective_dimensions:
            result.setdefault(dim, set()).add(name)
    return result


__all__ = [
    "DETECTORS",
    "DetectorMeta",
    "JUDGMENT_DETECTORS",
    "_DISPLAY_ORDER",
    "dimension_to_detectors",
    "detector_names",
    "get_detector_meta",
    "detector_tools",
    "dimension_action_type",
    "display_order",
]
