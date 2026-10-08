"""Shared TypeScript phase configuration values."""

from __future__ import annotations

import re

from desloppify.engine.detectors.base import ComplexitySignal, GodRule
from desloppify.languages._framework.node.js_text import code_text
from desloppify.languages.typescript.syntax.scanner import SourceText


def _compute_ts_destructure_props(content, lines):
    long_destructures = re.findall(r"\{\s*(\w+(?:\s*,\s*\w+){8,})\s*\}", code_text(content))
    if not long_destructures:
        return None
    max_props = max(len(d.split(",")) for d in long_destructures)
    return max_props, f"destructure w/{max_props} props"


def _compute_ts_inline_types(content, lines):
    inline_types = len(
        re.findall(r"^(?:export\s+)?(?:type|interface)\s+\w+", code_text(content), re.MULTILINE)
    )
    if inline_types > 3:
        return inline_types, f"{inline_types} inline types"
    return None


def _code_signal(name: str, pattern: str, *, weight: int, threshold: int, comments: bool = False) -> ComplexitySignal:
    """A signal counting ``pattern`` where it starts in code, or with ``comments``, in a comment."""
    regex = re.compile(pattern, re.MULTILINE)

    def compute(content, lines):
        if comments:
            source = SourceText(content)
            count = sum(1 for match in regex.finditer(content) if source.kind_at(match.start()) == "comment")
        else:
            count = len(regex.findall(code_text(content)))
        return (count, f"{count} {name}") if count > threshold else None

    return ComplexitySignal(name, None, weight=weight, threshold=threshold, compute=compute)


TS_COMPLEXITY_SIGNALS = [
    _code_signal("imports", r"^import\s", weight=1, threshold=15),
    ComplexitySignal(
        "destructured props",
        None,
        weight=1,
        threshold=8,
        compute=_compute_ts_destructure_props,
    ),
    _code_signal("useEffects", r"useEffect\s*\(", weight=3, threshold=3),
    ComplexitySignal(
        "inline types", None, weight=1, threshold=3, compute=_compute_ts_inline_types
    ),
    _code_signal("TODOs", r"//\s*(?:TODO|FIXME|HACK|XXX)", weight=2, threshold=0, comments=True),
    _code_signal(
        "nested ternaries", r"[^?]\?[^?.:\n][^:\n]*[^?]\?[^?.]", weight=3, threshold=2
    ),
    _code_signal("useRefs", r"\buseRef\s*[<(]", weight=2, threshold=6),
]

TS_GOD_RULES = [
    GodRule("context_hooks", "context hooks", lambda c: c.metrics.get("context_hooks", 0), 3),
    GodRule("use_effects", "useEffects", lambda c: c.metrics.get("use_effects", 0), 4),
    GodRule("use_states", "useStates", lambda c: c.metrics.get("use_states", 0), 5),
    GodRule("custom_hooks", "custom hooks", lambda c: c.metrics.get("custom_hooks", 0), 8),
    GodRule("hook_total", "total hooks", lambda c: c.metrics.get("hook_total", 0), 10),
]

TS_SKIP_NAMES = {
    f"{stem}{ext}"
    for stem in ("index", "types", "constants", "utils", "helpers", "settings", "main")
    for ext in (".ts", ".tsx", ".js", ".jsx")
} | {"App.tsx", "App.jsx", "vite-env.d.ts"}

TS_SKIP_DIRS = {"src/shared/components/ui"}


__all__ = [
    "TS_COMPLEXITY_SIGNALS",
    "TS_GOD_RULES",
    "TS_SKIP_DIRS",
    "TS_SKIP_NAMES",
]
