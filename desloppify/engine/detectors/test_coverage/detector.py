"""Test coverage gap detection — static analysis of test mapping and quality."""

from __future__ import annotations

from dataclasses import dataclass, field

from desloppify.engine.detectors.coverage.mapping import (
    analyze_test_quality,
    import_based_mapping,
    naming_based_mapping,
    transitive_coverage,
)
from desloppify.engine.detectors.coverage.mapping_imports import (
    _discover_additional_test_mapping_files,
)
from desloppify.engine.policy.zones import FileZoneMap

from .discovery import (
    _discover_scorable_and_tests,
    _no_tests_issues,
    _normalize_graph_paths,
)
from .heuristics import _has_inline_tests, _public_entry_files
from .issues import (
    _generate_issues,
)



@dataclass
class CoverageResult:
    """Issues, scoring potential (√LOC-weighted) and how many files were scored."""

    entries: list[dict] = field(default_factory=list)
    potential: int = 0
    scored_files: int = 0


def detect_test_coverage(
    graph: dict,
    zone_map: FileZoneMap,
    lang_name: str,
    extra_test_files: set[str] | None = None,
    complexity_map: dict[str, float] | None = None,
) -> tuple[list[dict], int]:
    result = run_test_coverage(
        graph, zone_map, lang_name, extra_test_files=extra_test_files, complexity_map=complexity_map
    )
    return result.entries, result.potential


def run_test_coverage(
    graph: dict,
    zone_map: FileZoneMap,
    lang_name: str,
    extra_test_files: set[str] | None = None,
    complexity_map: dict[str, float] | None = None,
) -> CoverageResult:
    graph = _normalize_graph_paths(graph)

    production_files, test_files, scorable, potential = _discover_scorable_and_tests(
        graph=graph,
        zone_map=zone_map,
        lang_name=lang_name,
        extra_test_files=extra_test_files,
    )
    if not scorable:
        return CoverageResult()

    inline_tested = {
        filepath
        for filepath in scorable
        if filepath in production_files and _has_inline_tests(filepath, lang_name)
    }

    if not test_files and not inline_tested:
        entries = _no_tests_issues(scorable, graph, lang_name, complexity_map)
        return CoverageResult(entries, potential, len(scorable))

    mapping_test_files = set(test_files)
    if test_files:
        mapping_test_files |= _discover_additional_test_mapping_files(
            test_files,
            production_files,
            lang_name,
        )

    directly_tested = set(inline_tested)
    if mapping_test_files:
        directly_tested |= import_based_mapping(
            graph,
            mapping_test_files,
            production_files,
            lang_name,
        )
    if test_files:
        directly_tested |= naming_based_mapping(test_files, production_files, lang_name)

    transitively_tested = transitive_coverage(directly_tested, graph, production_files)
    # A tested public entry exercises what it imports: tests through the
    # package's API count as covering the modules behind it.
    tested_entries = directly_tested & _public_entry_files(production_files, lang_name)
    covered_via_entry = transitively_tested & transitive_coverage(tested_entries, graph, production_files)
    transitively_tested -= covered_via_entry
    test_quality = analyze_test_quality(test_files, lang_name)

    entries = _generate_issues(
        scorable - covered_via_entry,
        directly_tested,
        transitively_tested,
        test_quality,
        graph,
        lang_name,
        complexity_map=complexity_map,
    )
    return CoverageResult(entries, potential, len(scorable))
