"""Coupling and dependency-oriented TypeScript phase helpers."""

from __future__ import annotations

from pathlib import Path

import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.packages as packages_mod
import desloppify.languages.typescript.detectors.facade as facade_detector_mod
import desloppify.languages.typescript.detectors.knip_adapter as knip_adapter_mod
import desloppify.languages.typescript.detectors.patterns.analysis as patterns_detector_mod
from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.sfc import SFC_SUFFIXES
from desloppify.base.output.terminal import log
from desloppify.engine._state.filtering import make_issue
from desloppify.engine.detectors import coupling as coupling_detector_mod
from desloppify.engine.detectors import graph as graph_detector_mod
from desloppify.engine.detectors import naming as naming_detector_mod
from desloppify.engine.detectors import orphaned as orphaned_detector_mod
from desloppify.engine.detectors import single_use as single_use_detector_mod
from desloppify.engine.detectors.coupling import Layer
from desloppify.engine.policy.zones import adjust_potential, filter_entries
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.languages._framework.frameworks.detection import (
    injected_class_decorators,
)
from desloppify.languages._framework.frameworks.registry import (
    framework_entry_conventions,
)
from desloppify.languages._framework.issue_factories import (
    make_cycle_issues,
    make_facade_issues,
    make_orphaned_issues,
    make_single_use_issues,
)
from desloppify.languages._framework.node.js_classes import iter_classes
from desloppify.languages.typescript.detectors.deps.auto_imports import (
    auto_import_entries,
)
from desloppify.languages.typescript.detectors.patterns.catalog import (
    configured_pattern_families,
)
from desloppify.languages.typescript.phases_config import TS_SKIP_NAMES
from desloppify.languages.typescript.plugin_contract import TS_BARREL_NAMES
from desloppify.languages.typescript.presets import resolve_layers, shadcn_ui_dirs
from desloppify.state_io import Issue


def detect_single_use(
    path: Path, graph: dict, lang: LangRuntimeContract
) -> tuple[list[Issue], list[dict], int]:
    """Detect single-use abstractions."""
    single_entries, single_candidates = (
        single_use_detector_mod.detect_single_use_abstractions(
            path, graph, barrel_names=lang.barrel_names
        )
    )
    single_entries = filter_entries(lang.zone_map, single_entries, "single_use")
    decorators = injected_class_decorators(path, lang)
    if decorators:
        single_entries = [
            e
            for e in single_entries
            if not _declares_injected_class(e["file"], decorators)
        ]
    issues = make_single_use_issues(
        single_entries, lang.get_area, skip_dir_names={"commands"}, stderr_fn=log
    )
    return issues, single_entries, single_candidates


def _declares_injected_class(filepath: str, decorators: frozenset[str]) -> bool:
    """Whether the file declares a class the framework's DI container wires."""
    try:
        text = Path(resolve_path(filepath)).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return any(cls.has_decorator(decorators) for cls in iter_classes(text))


def detect_layer_issues(
    path: Path,
    graph: dict,
    lang: LangRuntimeContract,
    layers: tuple[Layer, ...],
) -> tuple[list[Issue], int]:
    """Imports up the layer stack and across slices of a layer."""
    if not layers:
        return [], 0
    entries, edge_counts = coupling_detector_mod.detect_layer_violations(
        path, graph, layers
    )
    entries = filter_entries(lang.zone_map, entries, "coupling")
    results: list[Issue] = []
    for entry in entries:
        if entry["kind"] == "upward":
            summary = (
                f"Layer violation: {entry['layer']} imports {entry['target_layer']} "
                f"({entry['target']})"
            )
            detail = {
                key: entry[key]
                for key in ("target", "layer", "target_layer", "slice", "direction")
            }
        else:
            summary = (
                f"Cross-slice import in {entry['layer']}: "
                f"{entry['source_slice']}→{entry['target_slice']} ({entry['target']})"
            )
            detail = {
                key: entry[key]
                for key in (
                    "target",
                    "layer",
                    "source_slice",
                    "target_slice",
                    "direction",
                )
            }
        results.append(
            make_issue(
                "coupling",
                entry["file"],
                entry["target"],
                tier=2,
                confidence="high",
                summary=summary,
                detail=detail,
            )
        )
    cross = sum(1 for entry in entries if entry["kind"] == "cross_slice")
    if cross:
        log(f"         cross-slice: {cross} imports")
    return results, edge_counts.eligible_edges


def package_context(
    path: Path, graph: dict
) -> tuple[list[packages_mod.Package], packages_mod.PackageEntries]:
    """Workspace packages for the scan and the entry files their manifests name."""
    packages = packages_mod.discover_packages(path, get_project_root())
    return packages, packages_mod.workspace_entries(packages, sorted(graph))


def _specifier_could_name(specifier: str, filepath: str) -> bool:
    """Whether ``~/lib/format`` (alias stripped) could be ``.../lib/format.ts``."""
    parts = specifier.split("/")
    rest = parts[2:] if specifier.startswith("@") and len(parts) > 2 else parts[1:]
    if not rest or not rest[-1]:
        return False
    tail = "/" + "/".join(rest)
    path = Path(filepath)
    stem_path = str(path.with_suffix("")).replace("\\", "/")
    if stem_path.endswith(tail):
        return True
    return path.stem == "index" and str(path.parent).replace("\\", "/").endswith(tail)


def flag_unresolved_orphans(entries: list[dict], graph: dict) -> None:
    """Lower confidence for orphans an unresolved import may actually reach.

    A safety net while the resolver matures: an alias it can't map leaves the
    target with no importers, so the orphan finding is less certain.
    """
    unresolved: dict[str, set[str]] = {}
    for source, node in graph.items():
        for specifier in node.get("unresolved_imports", ()):
            unresolved.setdefault(specifier, set()).add(source)
    if not unresolved:
        return
    for entry in entries:
        sources = {
            source
            for specifier, importers in unresolved.items()
            if _specifier_could_name(specifier, entry["file"])
            for source in importers
        }
        if sources:
            entry["confidence"] = "low"
            entry["possible_importers"] = sorted(rel(s) for s in sources)


def corroborate_orphans_with_knip(
    entries: list[dict], path: Path, lang: LangRuntimeContract
) -> None:
    """Check orphans against Knip's unused files, from the scan's shared Knip run.

    Knip agreeing raises an orphan to high confidence, unless Knip also can't
    resolve an import that may point at it. A file Knip doesn't report is one
    it reaches from an entry point it knows (a plugin's config, a manifest
    field) or ignores by config, so the orphan drops to low confidence.
    """
    run = knip_adapter_mod.run_knip(path, cache=lang.runtime_cache)
    if not run.usable:
        return
    unused = knip_adapter_mod.unused_files(run)
    unresolved = knip_adapter_mod.unresolved_imports(run)
    for entry in entries:
        if str(Path(resolve_path(entry["file"])).resolve()) not in unused:
            entry["knip"] = "reachable"
            entry["confidence"] = "low"
            continue
        entry["knip"] = "unused"
        possible = entry.get("possible_importers") or ()
        still_unresolved = any(
            _specifier_could_name(specifier, entry["file"])
            for importer in possible
            for specifier in unresolved.get(
                str(Path(resolve_path(importer)).resolve()), ()
            )
        )
        if not still_unresolved:
            entry["confidence"] = "high"


def find_orphans(
    path: Path,
    graph: dict,
    lang: LangRuntimeContract,
    packages: list[packages_mod.Package] | None = None,
    entries: packages_mod.PackageEntries | None = None,
) -> tuple[list[dict], int]:
    """Orphaned-file entries as the scan reports them (zones applied, confidence set).

    Returns (entries, total_graph_files). ``detect orphaned`` uses this too,
    so it and ``scan`` agree.
    """
    orphan_entries, total_graph_files = orphaned_detector_mod.detect_orphaned_files(
        path,
        graph,
        extensions=[*lang.extensions, *SFC_SUFFIXES],
        options=orphaned_detector_mod.OrphanedDetectionOptions(
            extra_entry_patterns=lang.entry_patterns,
            extra_barrel_names=lang.barrel_names,
            # Dynamic imports, import.meta.glob and mocks are graph edges
            # already, so no suffix-matched dynamic import fallback here.
            entry_files=(entries.all if entries else set()) | auto_import_entries(path),
            package_roots=[p.directory for p in packages or ()],
            entry_conventions=framework_entry_conventions(),
        ),
    )
    orphan_entries = filter_entries(lang.zone_map, orphan_entries, "orphaned")
    flag_unresolved_orphans(orphan_entries, graph)
    corroborate_orphans_with_knip(orphan_entries, path, lang)
    return orphan_entries, total_graph_files


def detect_cycles_and_orphans(
    path: Path,
    graph: dict,
    lang: LangRuntimeContract,
    packages: list[packages_mod.Package] | None = None,
    entries: packages_mod.PackageEntries | None = None,
) -> tuple[list[Issue], int]:
    """Detect import cycles and orphaned files."""
    results: list[Issue] = []
    cycle_entries, _ = graph_detector_mod.detect_cycles(graph)
    cycle_entries = filter_entries(
        lang.zone_map, cycle_entries, "cycles", file_key="files"
    )
    results.extend(make_cycle_issues(cycle_entries, log))

    orphan_entries, total_graph_files = find_orphans(
        path, graph, lang, packages, entries
    )
    results.extend(make_orphaned_issues(orphan_entries, log))
    return results, total_graph_files


def detect_facades(
    graph: dict, lang: LangRuntimeContract, public_entries: set[str] | None = None
) -> list[Issue]:
    """Detect re-export facade files.

    A package's public entry (``exports``/``main``) re-exporting its modules
    is the package's API, not an indirection layer.
    """
    facade_entries, _ = facade_detector_mod.detect_reexport_facades(graph)
    if public_entries:
        facade_entries = [e for e in facade_entries if e["file"] not in public_entries]
    facade_entries = filter_entries(lang.zone_map, facade_entries, "facade")
    return make_facade_issues(facade_entries, log)


def detect_pattern_anomalies(
    path: Path, lang: LangRuntimeContract | None = None
) -> tuple[list[Issue], int]:
    """Detect competing patterns across areas, for the families the project configures."""
    families = configured_pattern_families(lang)
    if not families:
        return [], 0
    pattern_result = patterns_detector_mod.detect_pattern_anomalies(path, families)
    pattern_entries = pattern_result.entries
    total_areas = pattern_result.population_size
    results: list[Issue] = []
    for entry in pattern_entries:
        results.append(
            make_issue(
                "patterns",
                entry["area"],
                entry["family"],
                tier=3,
                confidence=entry.get("confidence", "low"),
                summary=f"Competing patterns ({entry['family']}): {entry['review'][:120]}",
                detail={
                    "family": entry["family"],
                    "patterns_used": entry["patterns_used"],
                    "pattern_count": entry["pattern_count"],
                    "review": entry["review"],
                },
            )
        )
    return results, total_areas


def detect_naming_inconsistencies(
    path: Path, lang: LangRuntimeContract
) -> tuple[list[Issue], int]:
    """Detect naming convention inconsistencies within directories."""
    naming_entries, total_dirs = naming_detector_mod.detect_naming_inconsistencies(
        path,
        file_finder=lang.file_finder,
        skip_names=TS_SKIP_NAMES,
        skip_dirs=set(shadcn_ui_dirs()),
    )
    results: list[Issue] = []
    for entry in naming_entries:
        results.append(
            make_issue(
                "naming",
                entry["directory"],
                entry["minority"],
                tier=3,
                confidence="low",
                summary=(
                    f"Naming inconsistency: {entry['minority_count']} {entry['minority']} files "
                    f"in {entry['majority']}-majority dir ({entry['total_files']} total)"
                ),
                detail={
                    "majority": entry["majority"],
                    "majority_count": entry["majority_count"],
                    "minority": entry["minority"],
                    "minority_count": entry["minority_count"],
                    "outliers": entry["outliers"],
                },
            )
        )
    return results, total_dirs


def make_boundary_issues(
    single_entries: list[dict],
    path: Path,
    graph: dict,
    lang: LangRuntimeContract,
    layers: tuple[Layer, ...],
) -> tuple[list[Issue], int]:
    """Create boundary-candidate issues, deduplicated against single-use."""
    if not layers:
        return [], 0
    single_use_emitted = set()
    for entry in single_entries:
        is_size_ok = 50 <= entry["loc"] <= 200
        is_colocated = lang.get_area and (
            lang.get_area(rel(entry["file"])) == lang.get_area(entry["sole_importer"])
        )
        if not is_size_ok and not is_colocated:
            single_use_emitted.add(rel(entry["file"]))

    results: list[Issue] = []
    deduped = 0
    boundary_entries, total_shared = coupling_detector_mod.detect_boundary_candidates(
        path,
        graph,
        layers,
        skip_basenames=TS_BARREL_NAMES,
        skip_dirs=shadcn_ui_dirs(),
    )
    for entry in boundary_entries:
        if rel(entry["file"]) in single_use_emitted:
            deduped += 1
            continue
        results.append(
            make_issue(
                "coupling",
                entry["file"],
                f"boundary::{entry['sole_slice']}",
                tier=3,
                confidence="medium",
                summary=(
                    f"Boundary candidate ({entry['loc']} LOC): only used by {entry['sole_slice']} "
                    f"({entry['importer_count']} importers)"
                ),
                detail={
                    "sole_slice": entry["sole_slice"],
                    "importer_count": entry["importer_count"],
                    "loc": entry["loc"],
                },
            )
        )
    if deduped:
        log(f"         ({deduped} boundary candidates skipped — covered by single_use)")
    return results, total_shared


def phase_coupling(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Run the coupling phase."""
    results: list[Issue] = []
    graph = deps_detector_mod.build_dep_graph(path)
    lang.dep_graph = graph
    zone_map = lang.zone_map

    single_use_issues, single_entries, single_candidates = detect_single_use(
        path, graph, lang
    )
    results.extend(single_use_issues)

    layers = resolve_layers(path, lang)
    coupling_issues, coupling_edges = detect_layer_issues(path, graph, lang, layers)
    results.extend(coupling_issues)

    boundary_issues, _ = make_boundary_issues(single_entries, path, graph, lang, layers)
    results.extend(boundary_issues)

    packages, entries = package_context(path, graph)
    cycle_orphan_issues, total_graph_files = detect_cycles_and_orphans(
        path, graph, lang, packages, entries
    )
    results.extend(cycle_orphan_issues)

    results.extend(detect_facades(graph, lang, entries.public))

    pattern_issues, total_areas = detect_pattern_anomalies(path, lang)
    results.extend(pattern_issues)

    naming_issues, total_dirs = detect_naming_inconsistencies(path, lang)
    results.extend(naming_issues)

    log(f"         → {len(results)} coupling/structural issues total")
    potentials = {
        "single_use": adjust_potential(zone_map, single_candidates),
        "coupling": coupling_edges,
        "cycles": adjust_potential(zone_map, total_graph_files),
        "orphaned": adjust_potential(zone_map, total_graph_files),
        "patterns": total_areas,
        "naming": total_dirs,
        "facade": adjust_potential(zone_map, total_graph_files),
    }
    return results, potentials


__all__ = [
    "coupling_detector_mod",
    "detect_layer_issues",
    "detect_cycles_and_orphans",
    "detect_facades",
    "detect_naming_inconsistencies",
    "detect_pattern_anomalies",
    "detect_single_use",
    "make_boundary_issues",
    "orphaned_detector_mod",
    "package_context",
    "phase_coupling",
]
