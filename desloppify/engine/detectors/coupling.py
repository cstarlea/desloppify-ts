"""Coupling analysis: layer violations, cross-slice imports and boundary candidates.

A project's architecture is a stack of layers, top first. A layer may import
the layers below it, never one above. A *sliced* layer holds independent
slices (one directory each: a feature, a page, an entity) that must not
import each other. The layers come from the caller (a preset or the
project's config); the algorithms work on any dependency graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import count_lines, rel, resolve_scan_file
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.output.fallbacks import log_best_effort_failure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Layer:
    """One architectural layer: directory prefixes, relative to the project root."""

    name: str
    paths: tuple[str, ...]
    sliced: bool = False
    # A directory inside a slice that other slices of the layer may import
    # (Feature-Sliced Design's ``@x`` cross-import notation).
    cross_import_dir: str | None = None


@dataclass(frozen=True)
class CouplingEdgeCounts:
    """Explicit coupling-edge counters.

    `eligible_edges` tracks the denominator universe for a detector.
    `violating_edges` tracks only edges that violate the rule.
    """

    violating_edges: int = 0
    eligible_edges: int = 0


@dataclass(frozen=True)
class _Location:
    layer: int
    prefix: str
    slice: str | None
    rest: str


def _norm_path(path: str) -> str:
    """Normalize path separators for cross-platform prefix matching."""
    return path.replace("\\", "/")


def _norm_root(path: Path) -> str:
    """Normalize project root path for absolute/relative prefix matching."""
    try:
        root = _norm_path(str(path.resolve()))
    except OSError:
        root = _norm_path(str(path))
    return root if root.endswith("/") else root + "/"


class _Locator:
    """Places files in layers; longest matching prefix wins."""

    def __init__(self, layers: tuple[Layer, ...] | list[Layer]) -> None:
        self.layers = tuple(layers)
        self.root = _norm_root(get_project_root())
        prefixes: list[tuple[str, int]] = []
        for index, layer in enumerate(self.layers):
            for raw in layer.paths:
                prefix = _norm_path(str(raw).strip()).strip("/")
                if prefix.startswith(self.root):
                    prefix = prefix[len(self.root) :]
                if prefix:
                    prefixes.append((prefix + "/", index))
        self._prefixes = sorted(prefixes, key=lambda item: -len(item[0]))

    def locate(self, filepath: str) -> _Location | None:
        value = _norm_path(filepath)
        if value.startswith(self.root):
            value = value[len(self.root) :]
        value = value.lstrip("/")
        for prefix, index in self._prefixes:
            if not value.startswith(prefix):
                continue
            rest = value[len(prefix) :]
            slice_name = (
                rest.split("/")[0]
                if self.layers[index].sliced and "/" in rest
                else None
            )
            return _Location(index, prefix, slice_name, rest)
        return None


def _is_cross_import(layer: Layer, target: _Location) -> bool:
    if not layer.cross_import_dir:
        return False
    return layer.cross_import_dir in target.rest.split("/")[1:-1]


def detect_layer_violations(
    path: Path, graph: dict, layers: tuple[Layer, ...] | list[Layer]
) -> tuple[list[dict], CouplingEdgeCounts]:
    """Imports that go up the layer stack, or across slices of one layer.

    Every import between two files in layers is an eligible edge.
    """
    locator = _Locator(layers)
    violating_edges = 0
    eligible_edges = 0
    entries: list[dict] = []
    for filepath, node in graph.items():
        source = locator.locate(filepath)
        if source is None:
            continue
        source_layer = locator.layers[source.layer]
        for target_path in node.get("imports", ()):
            target = locator.locate(target_path)
            if target is None:
                continue
            eligible_edges += 1
            target_layer = locator.layers[target.layer]
            if target.layer < source.layer:
                violating_edges += 1
                entries.append(
                    {
                        "file": filepath,
                        "target": rel(target_path),
                        "kind": "upward",
                        "layer": source_layer.name,
                        "target_layer": target_layer.name,
                        "slice": target.slice,
                        "direction": f"{source_layer.name}→{target_layer.name}",
                    }
                )
            elif (
                target.layer == source.layer
                and source.slice
                and target.slice
                and source.slice != target.slice
                and not _is_cross_import(source_layer, target)
            ):
                violating_edges += 1
                entries.append(
                    {
                        "file": filepath,
                        "target": rel(target_path),
                        "kind": "cross_slice",
                        "layer": source_layer.name,
                        "source_slice": source.slice,
                        "target_slice": target.slice,
                        "direction": f"{source.slice}→{target.slice}",
                    }
                )
    entries.sort(key=lambda e: (e["file"], e["target"]))
    return entries, CouplingEdgeCounts(
        violating_edges=violating_edges, eligible_edges=eligible_edges
    )


def detect_boundary_candidates(
    path: Path,
    graph: dict,
    layers: tuple[Layer, ...] | list[Layer],
    skip_basenames: set[str] | None = None,
    skip_dirs: tuple[str, ...] = (),
) -> tuple[list[dict], int]:
    """Files of an unsliced layer imported only from one slice of a layer above.

    Such a file could live in that slice. ``skip_dirs`` (project-relative)
    hold files shared by intent, such as a generated UI kit.
    Returns (entries, files in unsliced layers).
    """
    locator = _Locator(layers)
    skip_basenames = skip_basenames or set()
    skipped = tuple(d.strip("/") + "/" for d in skip_dirs if d.strip("/"))
    total = 0
    entries: list[dict] = []
    for filepath, node in graph.items():
        location = locator.locate(filepath)
        if location is None or locator.layers[location.layer].sliced:
            continue
        total += 1
        if Path(filepath).name in skip_basenames:
            continue
        if skipped and rel(filepath).replace("\\", "/").startswith(skipped):
            continue
        if node.get("importer_count", 0) == 0:
            continue
        owners: set[tuple[str, str]] = set()
        for importer in node.get("importers", ()):
            source = locator.locate(importer)
            if source is None or source.slice is None or source.layer >= location.layer:
                owners.add(("", ""))
                break
            owners.add((source.prefix, source.slice))
        if len(owners) != 1 or ("", "") in owners:
            continue
        prefix, slice_name = next(iter(owners))
        try:
            resolved = resolve_scan_file(filepath, scan_root=path)
            if not resolved.exists():
                raise FileNotFoundError(resolved)
            loc = count_lines(resolved)
        except (OSError, UnicodeDecodeError) as exc:
            log_best_effort_failure(
                logger, f"read coupling detector candidate {filepath}", exc
            )
            continue
        entries.append(
            {
                "file": filepath,
                "sole_slice": f"{prefix}{slice_name}",
                "importer_count": node.get("importer_count", 0),
                "loc": loc,
            }
        )
    return sorted(entries, key=lambda e: (-e["loc"], e["file"])), total


__all__ = [
    "CouplingEdgeCounts",
    "Layer",
    "detect_boundary_candidates",
    "detect_layer_violations",
]
