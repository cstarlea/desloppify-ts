"""Pattern families for the TypeScript pattern consistency analysis.

A family is a set of approaches to one job (data fetching, settings
persistence, toasts) that a project wants to standardise on. Which hooks and
helpers compete is the project's own knowledge, so nothing is built in: the
families come from ``languages.typescript.pattern_families``::

    {"settings_persistence": {
        "description": "Pick one settings hook",
        "threshold": 2,
        "patterns": {"useAutoSave": "\\\\buseAutoSave\\\\(", "useLocalState": "\\\\buseLocalState\\\\("}}}

``type`` defaults to ``competing`` (an area using ``threshold`` or more of
them, or a pattern used in under 10% of areas, is reported); a
``complementary`` family is only counted in ``detect patterns``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TYPES = ("competing", "complementary")


def normalize_families(raw: object) -> dict[str, dict[str, Any]]:
    """Valid families from a config value; invalid entries are logged and dropped."""
    if not isinstance(raw, dict):
        return {}
    families: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        patterns = spec.get("patterns") if isinstance(spec, dict) else None
        if not isinstance(patterns, dict) or not patterns:
            logger.warning("pattern_families.%s: no patterns", name)
            continue
        valid: dict[str, str] = {}
        for label, regex in patterns.items():
            try:
                re.compile(str(regex))
            except re.error as exc:
                logger.warning(
                    "pattern_families.%s.%s: bad regex (%s)", name, label, exc
                )
                continue
            valid[str(label)] = str(regex)
        if not valid:
            continue
        kind = spec.get("type", "competing")
        threshold = spec.get("threshold", 2)
        families[str(name)] = {
            "type": kind if kind in _TYPES else "competing",
            "description": str(spec.get("description") or name),
            "fragmentation_threshold": threshold
            if isinstance(threshold, int) and threshold > 1
            else 2,
            "patterns": valid,
        }
    return families


def configured_pattern_families(lang: object) -> dict[str, dict[str, Any]]:
    """The families ``languages.typescript.pattern_families`` defines."""
    getter = getattr(lang, "runtime_setting", None)
    return normalize_families(getter("pattern_families") if callable(getter) else None)


__all__ = ["configured_pattern_families", "normalize_families"]
