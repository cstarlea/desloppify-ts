"""Responsibility cohesion detection via tree-sitter function extraction.

Identifies files with multiple disconnected clusters of functions —
a sign of mixed responsibilities ("dumping ground" modules).

Algorithm:
1. Extract all top-level functions in each file
2. Build an intra-file call graph (function A references function B's name)
3. Find connected components via union-find
4. Flag files with 5+ disconnected clusters
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from desloppify.base.output.terminal import log
from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.base.types import DetectorPhase
from desloppify.state_io import Issue

from .parsing import (
    _make_query,
    _node_text,
    _run_query,
    _unwrap_node,
)

if TYPE_CHECKING:
    from desloppify.languages._framework.base.types import LangRuntimeContract

    from .spec import TreeSitterLangSpec


# Minimum thresholds to analyze a file.
_MIN_FUNCTIONS = 8  # Don't flag files with few functions.
_MIN_CLUSTERS = 5  # Minimum disconnected clusters to flag.
_MIN_NON_SINGLETON_CLUSTERS = 3  # Minimum multi-function clusters to flag.


def detect_responsibility_cohesion(
    file_list: list[str],
    spec: TreeSitterLangSpec,
    *,
    min_loc: int = 200,
) -> tuple[list[dict], int]:
    """Find files with disconnected function clusters.

    Returns (entries, total_files_checked).
    Each entry: {file, loc, function_count, component_count, families}.
    """
    queries: dict[object, object] = {}
    entries: list[dict] = []
    checked = 0

    for filepath in file_list:
        parsed = spec.parse_file(filepath)
        if parsed is None:
            continue
        source, tree = parsed
        query = queries.get(tree.language)
        if query is None:
            query = queries[tree.language] = _make_query(
                tree.language, spec.function_query
            )
        checked += 1

        loc = source.count(b"\n") + 1
        if loc < min_loc:
            continue

        # Extract all top-level function names and bodies.
        matches = _run_query(query, tree.root_node)
        functions: dict[str, str] = {}  # name -> body_text
        for _pattern_idx, captures in matches:
            func_node = _unwrap_node(captures.get("func"))
            name_node = _unwrap_node(captures.get("name"))
            if not func_node or not name_node:
                continue
            name = _node_text(name_node)
            body = source[func_node.start_byte : func_node.end_byte]
            functions[name] = body.decode("utf-8", errors="replace")

        if len(functions) < _MIN_FUNCTIONS:
            continue

        # Build intra-file call graph: function A references function B.
        func_names = set(functions.keys())
        adjacency: dict[str, set[str]] = defaultdict(set)

        for fn_name, body in functions.items():
            for other_name in func_names:
                if other_name == fn_name:
                    continue
                # Check if the function body references the other function name.
                # Use word boundary matching to avoid substring false positives.
                if re.search(r"\b" + re.escape(other_name) + r"\b", body):
                    adjacency[fn_name].add(other_name)
                    adjacency[other_name].add(fn_name)

        # Find connected components via BFS.
        visited: set[str] = set()
        components: list[list[str]] = []
        for fn_name in func_names:
            if fn_name in visited:
                continue
            component: list[str] = []
            queue = [fn_name]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor in adjacency.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            components.append(component)

        if len(components) >= _MIN_CLUSTERS:
            # Sort components by size for reporting.
            components.sort(key=len, reverse=True)

            # Toolkit heuristic: "mixed responsibilities" means multiple
            # distinct groups of interrelated functions coexisting in one
            # file.  Singleton clusters (isolated utility functions) don't
            # count as separate "responsibilities" — they're standalone
            # helpers.  Only flag when there are ≥3 multi-function clusters,
            # indicating genuinely distinct groups of related code.
            non_singleton_count = sum(1 for c in components if len(c) > 1)
            if non_singleton_count < _MIN_NON_SINGLETON_CLUSTERS:
                continue

            families = [c[0] for c in components[:8]]  # Top 8 cluster names.

            entries.append(
                {
                    "file": filepath,
                    "loc": loc,
                    "function_count": len(functions),
                    "component_count": len(components),
                    "component_sizes": [len(c) for c in components],
                    "families": families,
                }
            )

    entries.sort(key=lambda e: -e["component_count"])
    return entries, checked


def make_cohesion_phase(spec: TreeSitterLangSpec) -> DetectorPhase:
    """Create a responsibility cohesion phase."""

    def run(
        path: Path, lang: LangRuntimeContract
    ) -> tuple[list[Issue], dict[str, int]]:
        file_list = lang.file_finder(path)
        issues: list[Issue] = []
        potentials: dict[str, int] = {}

        entries, _checked = detect_responsibility_cohesion(file_list, spec)
        for e in entries:
            families = ", ".join(e["families"][:4])
            issues.append(
                make_issue(
                    "responsibility_cohesion",
                    e["file"],
                    f"cohesion::{e['file']}",
                    tier=3,
                    confidence="medium",
                    summary=(
                        f"{e['component_count']} disconnected function clusters "
                        f"({e['function_count']} functions) — likely mixed responsibilities"
                    ),
                    detail={
                        "cluster_count": e["component_count"],
                        "family": families,
                        "families": e["families"],
                    },
                )
            )
        if entries:
            potentials["responsibility_cohesion"] = len(entries)
            log(f"         low-cohesion files: {len(entries)}")

        return issues, potentials

    return DetectorPhase("Responsibility cohesion", run)


__all__ = ["detect_responsibility_cohesion", "make_cohesion_phase"]
