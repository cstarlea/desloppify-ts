"""Data collection and tree-building helpers for visualization output."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import desloppify.languages.framework as lang_api
from desloppify.base.discovery.file_paths import rel, resolve_scan_file
from desloppify.base.output.fallbacks import (
    log_best_effort_failure,
    warn_best_effort,
)

logger = logging.getLogger(__name__)


def _collect_file_data(path: Path, lang=None) -> list[dict]:
    """Collect LOC for all source files using the language's file finder."""
    resolved_lang = lang or lang_api.default_lang()
    source_files = resolved_lang.file_finder(path)
    files = []
    warned_read_failure = False
    for filepath in source_files:
        try:
            p = resolve_scan_file(filepath, scan_root=path)
            content = p.read_text()
            loc = len(content.splitlines())
            files.append(
                {
                    "path": rel(filepath),
                    "abs_path": str(p.resolve()),
                    "loc": loc,
                }
            )
        except (OSError, UnicodeDecodeError) as exc:
            log_best_effort_failure(
                logger, f"read visualization source file {filepath}", exc
            )
            if not warned_read_failure:
                warned_read_failure = True
                warn_best_effort(
                    "Some visualization source files could not be read; output may be incomplete."
                )
            continue
    return files


def scan_root_label(path: Path) -> tuple[str, str]:
    """``(prefix, name)`` for the tree root of a scan of *path*.

    *prefix* is the scanned directory relative to the project root ("." for
    the root itself), which file paths are made relative to. *name* labels the
    root node: that relative path, or the directory's own name when the scan
    covers the whole project or lies outside it.
    """
    resolved = path.resolve()
    prefix = rel(resolved)
    if prefix in ("", ".") or prefix.startswith("../") or Path(prefix).is_absolute():
        return ".", resolved.name or str(resolved)
    return prefix, prefix


def _build_tree(
    files: list[dict],
    dep_graph: dict,
    issues_by_file: dict,
    *,
    prefix: str = "src",
    name: str | None = None,
) -> dict:
    """Build nested tree structure for D3 treemap.

    The root node is the scanned directory: *prefix* is its project-relative
    path, stripped from file paths, and *name* its label (default *prefix*).
    """
    root: dict = {"name": name or prefix, "children": {}}
    prefix_parts = [] if prefix == "." else prefix.strip("/").split("/")

    for f in files:
        parts = f["path"].split("/")
        # Paths are project-relative; the root node already stands for the prefix.
        depth = len(prefix_parts)
        if depth and len(parts) > depth and parts[:depth] == prefix_parts:
            parts = parts[depth:]
        node = root
        for part in parts[:-1]:
            if part not in node["children"]:
                node["children"][part] = {"name": part, "children": {}}
            node = node["children"][part]

        filename = parts[-1]
        resolved = f["abs_path"]
        dep_entry = dep_graph.get(resolved, {"import_count": 0, "importer_count": 0})
        file_issues = issues_by_file.get(f["path"], [])
        open_issues = [ff for ff in file_issues if ff.get("status") == "open"]

        node["children"][filename] = {
            "name": filename,
            "path": f["path"],
            "loc": max(f["loc"], 1),  # D3 needs >0 values
            "fan_in": dep_entry.get("importer_count", 0),
            "fan_out": dep_entry.get("import_count", 0),
            "issues_total": len(file_issues),
            "issues_open": len(open_issues),
            "issue_summaries": [ff.get("summary", "") for ff in open_issues[:20]],
        }

    # Convert children dicts to arrays (D3 format)
    def to_array(node: dict[str, Any]) -> None:
        if "children" in node and isinstance(node["children"], dict):
            children = list(node["children"].values())
            for child in children:
                to_array(child)
            node["children"] = children
            # Remove empty directories
            node["children"] = [
                c
                for c in node["children"]
                if "loc" in c or ("children" in c and c["children"])
            ]

    to_array(root)
    return root


def _build_dep_graph_for_path(path: Path, lang) -> dict:
    """Build dependency graph using the resolved language plugin."""
    resolved_lang = lang or lang_api.default_lang()
    if resolved_lang and resolved_lang.build_dep_graph:
        try:
            return resolved_lang.build_dep_graph(path)
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            RuntimeError,
            TypeError,
        ) as exc:
            log_best_effort_failure(logger, "build visualization dependency graph", exc)
            warn_best_effort(
                "Could not build visualization dependency graph; showing file-only view."
            )
    return {}


def _issues_by_file(state: dict | None) -> dict[str, list]:
    """Group issues from state by file path."""
    result: dict[str, list] = defaultdict(list)
    work_items = (state.get("work_items") or state.get("issues", {})) if state else {}
    if work_items:
        for f in work_items.values():
            result[f["file"]].append(f)
    return result


__all__ = [
    "_build_dep_graph_for_path",
    "_build_tree",
    "_collect_file_data",
    "_issues_by_file",
    "scan_root_label",
]
