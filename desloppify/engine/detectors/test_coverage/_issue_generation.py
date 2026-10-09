"""Top-level issue generation for the test-coverage detector."""

from __future__ import annotations

from desloppify.engine.detectors.coverage.mapping import (
    build_test_import_index,
    get_test_files_for_prod,
)

from ._issue_gaps import (
    measured_coverage_issue,
    transitive_coverage_gap_issue,
    untested_module_issue,
)
from ._issue_quality import select_direct_test_quality_issue
from .metrics import _file_loc, _importer_count, _loc_weight
from .reports import FileCoverage


def generate_issues(
    scorable: set[str],
    directly_tested: set[str],
    transitively_tested: set[str],
    test_quality: dict[str, dict],
    graph: dict,
    lang_name: str,
    complexity_map: dict[str, float] | None = None,
    measured: dict[str, FileCoverage] | None = None,
) -> list[dict]:
    """Generate test-coverage issues for all scorable production files.

    A file with fresh measured coverage is judged by it instead of by the
    import graph; quality checks on its direct tests still apply.
    """
    entries: list[dict] = []
    cmap = complexity_map or {}
    measured = measured or {}
    test_files = set(test_quality.keys())
    production_scope = set(scorable) | set(directly_tested) | set(transitively_tested)
    parsed_imports_by_test = build_test_import_index(
        test_files,
        production_scope,
        lang_name,
    )

    for filepath in scorable:
        loc = _file_loc(filepath)
        importer_count = _importer_count(graph, filepath)
        loc_weight = _loc_weight(loc)

        if filepath in directly_tested:
            related_tests = get_test_files_for_prod(
                filepath,
                test_files,
                graph,
                lang_name,
                parsed_imports_by_test=parsed_imports_by_test,
            )
            issue = select_direct_test_quality_issue(
                prod_file=filepath,
                related_tests=related_tests,
                test_quality=test_quality,
                loc_weight=loc_weight,
            )
            if issue:
                entries.append(issue)
            if filepath not in measured:
                continue

        complexity = cmap.get(filepath, 0)
        if filepath in measured:
            gap = measured_coverage_issue(
                file_path=filepath,
                coverage=measured[filepath],
                loc=loc,
                importer_count=importer_count,
                loc_weight=loc_weight,
                complexity=complexity,
            )
            if gap:
                entries.append(gap)
            continue
        if filepath in transitively_tested:
            entries.append(
                transitive_coverage_gap_issue(
                    file_path=filepath,
                    loc=loc,
                    importer_count=importer_count,
                    loc_weight=loc_weight,
                    complexity=complexity,
                )
            )
            continue

        entries.append(
            untested_module_issue(
                file_path=filepath,
                loc=loc,
                importer_count=importer_count,
                loc_weight=loc_weight,
                complexity=complexity,
            )
        )

    return entries


__all__ = ["generate_issues"]
