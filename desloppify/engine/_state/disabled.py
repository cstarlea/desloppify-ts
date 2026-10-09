"""Detectors and mechanical dimensions disabled in config (``disabled``).

A disabled detector is out of scoring altogether: the scan drops its issues
and potential, and its existing issues are hidden (suppressed with a
``disabled:`` pattern) without changing their status. Wontfix issues are left
untouched. Re-enabling unhides them, and the next scan judges them as usual.
"""

from __future__ import annotations

from collections.abc import Iterable

from desloppify.base.registry import DETECTORS
from desloppify.engine._state.issue_semantics import is_scoring_excluded_detector
from desloppify.engine._state.schema import StateModel

DISABLED_PATTERN_PREFIX = "disabled:"
# Review findings are imported, not detected; review_dimensions governs them.
_NOT_DISABLEABLE = frozenset(
    {"review", "concerns", "subjective_review", "subjective_assessment"}
)


def _dimension_detectors() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, meta in DETECTORS.items():
        if is_scoring_excluded_detector(name) or not meta.dimension:
            continue
        grouped.setdefault(meta.dimension, []).append(name)
    return grouped


def _dimension_key(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").lower().split())


def canonical_disabled_entry(raw: str) -> str:
    """A detector name or mechanical dimension name, in canonical spelling."""
    text = raw.strip()
    if text in DETECTORS and text not in _NOT_DISABLEABLE:
        return text
    for dimension in _dimension_detectors():
        if _dimension_key(dimension) == _dimension_key(text):
            return dimension
    dimensions = ", ".join(_dimension_detectors())
    raise ValueError(
        f"Unknown detector or dimension: {raw!r}. Use a detector name (the part "
        f"before '::' in an issue ID) or a mechanical dimension: {dimensions}."
    )


def disabled_detectors(entries: Iterable[object]) -> set[str]:
    """Detector names covered by config entries; unknown entries are ignored."""
    by_dimension = _dimension_detectors()
    out: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        try:
            name = canonical_disabled_entry(entry)
        except ValueError:
            continue
        out.update(by_dimension.get(name, [name]))
    return out


def apply_disabled(
    state: StateModel, entries: Iterable[object], now: str
) -> tuple[int, int]:
    """Record the disabled detectors in state and hide or unhide their issues.

    Returns ``(hidden, restored)`` issue counts.
    """
    disabled = disabled_detectors(entries)
    state["disabled_detectors"] = sorted(disabled)
    hidden = restored = 0
    for issue in (state.get("work_items") or {}).values():
        if not isinstance(issue, dict):
            continue
        detector = issue.get("detector")
        pattern = str(issue.get("suppression_pattern") or "")
        was_disabled = pattern.startswith(DISABLED_PATTERN_PREFIX)
        if detector in disabled:
            if issue.get("status") == "wontfix" or issue.get("suppressed"):
                continue
            issue["suppressed"] = True
            issue["suppressed_at"] = now
            issue["suppression_pattern"] = f"{DISABLED_PATTERN_PREFIX}{detector}"
            hidden += 1
        elif was_disabled:
            issue["suppressed"] = False
            issue["suppressed_at"] = None
            issue["suppression_pattern"] = None
            restored += 1
    return hidden, restored


__all__ = [
    "DISABLED_PATTERN_PREFIX",
    "apply_disabled",
    "canonical_disabled_entry",
    "disabled_detectors",
]
