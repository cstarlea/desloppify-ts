"""Measured coverage from Istanbul and lcov reports, combined with the import graph."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from desloppify.base.discovery.source import find_source_files
from desloppify.engine.detectors.test_coverage.detector import run_test_coverage
from desloppify.engine.detectors.test_coverage.metrics import _loc_weight
from desloppify.engine.detectors.test_coverage.reports import (
    find_report_files,
    load_measured_coverage,
)
from desloppify.engine.policy.zones import FileZoneMap, Zone, ZoneRule

_LOGIC = (
    "export function run(n: number): number {\n"
    + "  n += 1;\n" * 10
    + "  return n;\n}\n"
)
_LINES = len(_LOGIC.splitlines())


@pytest.fixture(autouse=True)
def _root(set_project_root):
    yield


def _touch(root: Path, name: str, content: str = _LOGIC) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return name


def _age_sources(root: Path, seconds: float = 100) -> None:
    """Make every non-report file older than reports written afterwards."""
    past = time.time() - seconds
    for path in root.rglob("*.ts"):
        os.utime(path, (past, past))


def _istanbul_entry(
    path: Path, hit: set[int], lines: int = _LINES, branches: list[int] | None = None
) -> dict:
    statement_map = {
        str(i): {"start": {"line": i, "column": 0}, "end": {"line": i, "column": 1}}
        for i in range(1, lines + 1)
    }
    return {
        "path": str(path),
        "statementMap": statement_map,
        "s": {str(i): (1 if i in hit else 0) for i in range(1, lines + 1)},
        "branchMap": {"0": {}} if branches else {},
        "b": {"0": branches} if branches else {},
        "fnMap": {},
        "f": {},
    }


def _write_istanbul(report_dir: Path, entries: dict[Path, dict]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "coverage-final.json").write_text(
        json.dumps({str(p): e for p, e in entries.items()})
    )


def _write_lcov(
    report_dir: Path, records: dict[str, set[int]], lines: int = _LINES
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for sf, hit in records.items():
        out.append("TN:")
        out.append(f"SF:{sf}")
        out.extend(f"DA:{i},{1 if i in hit else 0}" for i in range(1, lines + 1))
        out.append("BRDA:2,0,0,1")
        out.append("BRDA:2,0,1,-")
        out.append("end_of_record")
    (report_dir / "lcov.info").write_text("\n".join(out) + "\n")


def _project(root: Path) -> tuple[dict, dict, FileZoneMap]:
    """test imports index -> core, util, gone; nothing imports orphan."""
    files = {
        "index": _touch(
            root,
            "src/index.ts",
            "import './core';\nimport './util';\nimport './gone';\n" + _LOGIC,
        ),
        "core": _touch(root, "src/core.ts"),
        "util": _touch(root, "src/util.ts"),
        "gone": _touch(root, "src/gone.ts"),
        "orphan": _touch(root, "src/orphan.ts"),
        "test": _touch(
            root,
            "test/index.test.ts",
            "import { run } from '../src/index';\nit('runs', () => { expect(run(1)).toBe(12); });\n",
        ),
    }
    graph = {
        files["index"]: {
            "imports": {files["core"], files["util"], files["gone"]},
            "importer_count": 1,
        },
        files["core"]: {"imports": set(), "importer_count": 1},
        files["util"]: {"imports": set(), "importer_count": 1},
        files["gone"]: {"imports": set(), "importer_count": 1},
        files["orphan"]: {"imports": set(), "importer_count": 0},
        files["test"]: {"imports": {files["index"]}},
    }
    zone_map = FileZoneMap(sorted(files.values()), [ZoneRule(Zone.TEST, [".test."])])
    return files, graph, zone_map


def _by_file(result) -> dict[str, dict]:
    return {e["file"]: e for e in result.entries}


def test_graph_only_without_a_report(tmp_path):
    files, graph, zone_map = _project(tmp_path)
    found = _by_file(run_test_coverage(graph, zone_map, "typescript"))
    assert found[files["core"]]["name"] == "transitive_only"
    assert found[files["orphan"]]["name"] == "untested_module"


def test_fresh_report_replaces_graph_verdicts(tmp_path):
    files, graph, zone_map = _project(tmp_path)
    _age_sources(tmp_path)
    all_lines = set(range(1, _LINES + 1))
    _write_istanbul(
        tmp_path / "coverage",
        {
            tmp_path / files["index"]: _istanbul_entry(
                tmp_path / files["index"], all_lines, lines=15
            ),
            tmp_path / files["core"]: _istanbul_entry(
                tmp_path / files["core"], all_lines
            ),
            tmp_path / files["util"]: _istanbul_entry(
                tmp_path / files["util"], set(range(1, 6)), branches=[1, 0, 0, 0]
            ),
            tmp_path / files["orphan"]: _istanbul_entry(
                tmp_path / files["orphan"], set()
            ),
        },
    )

    result = run_test_coverage(graph, zone_map, "typescript")
    found = _by_file(result)
    # Fully covered: the graph's transitive_only goes away.
    assert files["core"] not in found
    assert files["index"] not in found
    # 5 of 13 lines: below the 80% target, failing by the share it misses.
    low = found[files["util"]]
    assert low["name"] == "low_coverage"
    pct = 100 * 5 / _LINES
    assert low["detail"]["line_pct"] == round(pct, 1)
    assert low["detail"]["branch_pct"] == 25.0
    assert low["detail"]["loc_weight"] == pytest.approx(
        _loc_weight(_LINES) * (1 - pct / 80), abs=1e-3
    )
    # Never ran: keeps the graph's untested ID, now with the measurement.
    assert found[files["orphan"]]["name"] == "untested_module"
    assert found[files["orphan"]]["detail"]["line_pct"] == 0.0
    # Missing from the report: the graph decides.
    assert found[files["gone"]]["name"] == "transitive_only"
    assert "source" not in found[files["gone"]]["detail"]
    assert result.measured.reports == ["coverage/coverage-final.json"]


def test_files_changed_after_the_report_use_the_graph(tmp_path):
    files, graph, zone_map = _project(tmp_path)
    _age_sources(tmp_path)
    _write_istanbul(
        tmp_path / "coverage",
        {
            # The report has lines past core's end: it was run on other content.
            tmp_path / files["core"]: _istanbul_entry(
                tmp_path / files["core"], set(), lines=_LINES + 5
            ),
            tmp_path / files["util"]: _istanbul_entry(tmp_path / files["util"], set()),
        },
    )
    # util was edited after the run.
    (tmp_path / files["util"]).write_text(_LOGIC)
    future = time.time() + 10
    os.utime(tmp_path / files["util"], (future, future))

    result = run_test_coverage(graph, zone_map, "typescript")
    found = _by_file(result)
    assert found[files["core"]]["name"] == "transitive_only"
    assert found[files["util"]]["name"] == "transitive_only"
    assert result.measured.stale == {files["core"], files["util"]}
    assert result.measured.files == {}


def test_monorepo_package_reports_merge(tmp_path):
    a = _touch(tmp_path, "packages/a/src/a.ts")
    b = _touch(tmp_path, "packages/b/src/b.ts")
    _age_sources(tmp_path)
    # Package a: lcov with paths relative to the package.
    _write_lcov(tmp_path / "packages/a/coverage", {"src/a.ts": set(range(1, 4))})
    # Package b: Istanbul with absolute paths from a CI machine.
    ci_path = Path("/home/runner/work/repo/packages/b/src/b.ts")
    _write_istanbul(
        tmp_path / "packages/b/coverage",
        {ci_path: _istanbul_entry(ci_path, set(range(1, _LINES + 1)))},
    )
    # A root report from another run covers more of a: lines are merged.
    _write_lcov(tmp_path / "coverage", {"packages/a/src/a.ts": set(range(4, 12))})

    measured = load_measured_coverage({a, b})
    assert set(measured.files) == {a, b}
    assert measured.files[a].hit_lines == set(range(1, 12))
    assert measured.files[a].branch_pct == 50.0
    assert measured.files[b].line_pct == 100.0
    assert len(measured.reports) == 3


def test_configured_report_directories(tmp_path):
    _touch(
        tmp_path,
        "vitest.config.ts",
        "export default { test: { coverage: { reportsDirectory: './out/cov' } } };\n",
    )
    _touch(
        tmp_path,
        "pkg/package.json",
        json.dumps({"jest": {"coverageDirectory": "reports"}}),
    )
    (tmp_path / "out/cov").mkdir(parents=True)
    (tmp_path / "out/cov/lcov.info").write_text("")
    (tmp_path / "pkg/reports").mkdir(parents=True)
    (tmp_path / "pkg/reports/coverage-final.json").write_text("{}")
    (tmp_path / "pkg/reports/lcov.info").write_text("")

    found = {
        report.relative_to(tmp_path.resolve()).as_posix()
        for report, _base in find_report_files(tmp_path)
    }
    # The lcov twin of an Istanbul report is skipped.
    assert found == {"out/cov/lcov.info", "pkg/reports/coverage-final.json"}


def test_html_coverage_report_assets_are_not_source(tmp_path):
    _touch(tmp_path, "src/a.ts")
    for name in ("prettify.js", "sorter.js", "block-navigation.js"):
        _touch(tmp_path, f"coverage/lcov-report/{name}", "var x = 1;\n")
        _touch(tmp_path, f"html-cov/{name}", "var x = 1;\n")
    assert find_source_files(tmp_path, [".ts", ".js"]) == ["src/a.ts"]
