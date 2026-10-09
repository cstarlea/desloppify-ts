"""Coverage through a tested public entry, and the √LOC-weighted check labels."""

from __future__ import annotations

from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.app.commands.next.render_scoring import render_dimension_context
from desloppify.engine.detectors.test_coverage.detector import run_test_coverage
from desloppify.engine.policy.zones import FileZoneMap, Zone, ZoneRule
from desloppify.engine.scoring import is_loc_weighted_dimension
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.test_coverage import public_entry_files

_LOGIC = (
    "export function run(n: number): number {\n"
    + "  n += 1;\n" * 10
    + "  return n;\n}\n"
)


@pytest.fixture(autouse=True)
def _root(set_project_root):
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    yield
    clear_resolver_cache()


def _touch(root: Path, name: str, content: str = "") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return name


def _project(root: Path, manifest: str) -> tuple[dict, dict, FileZoneMap]:
    """api.test imports index (entry) -> core -> helper; util.test imports util -> internal."""
    _touch(root, "package.json", manifest)
    files = {
        "index": _touch(
            root,
            "src/index.ts",
            "import { run as c } from './core';\n"
            + _LOGIC.replace("n += 1", "n += c(n)"),
        ),
        "core": _touch(
            root, "src/core.ts", "import { run as h } from './helper';\n" + _LOGIC
        ),
        "helper": _touch(root, "src/helper.ts", _LOGIC),
        "util": _touch(
            root, "src/util.ts", "import { run as i } from './internal';\n" + _LOGIC
        ),
        "internal": _touch(root, "src/internal.ts", _LOGIC),
        "api_test": _touch(
            root,
            "test/api.test.ts",
            "import { run } from '../src';\nit('runs', () => { expect(run(1)).toBe(12); });\n",
        ),
        "util_test": _touch(
            root,
            "test/util.test.ts",
            "import { run } from '../src/util';\nit('runs', () => { expect(run(1)).toBe(12); });\n",
        ),
    }
    graph = {
        files["index"]: {"imports": {files["core"]}, "importer_count": 1},
        files["core"]: {"imports": {files["helper"]}, "importer_count": 1},
        files["helper"]: {"imports": set(), "importer_count": 1},
        files["util"]: {"imports": {files["internal"]}, "importer_count": 1},
        files["internal"]: {"imports": set(), "importer_count": 1},
        files["api_test"]: {"imports": {files["index"]}},
        files["util_test"]: {"imports": {files["util"]}},
    }
    zone_map = FileZoneMap(sorted(files.values()), [ZoneRule(Zone.TEST, [".test."])])
    return files, graph, zone_map


def _kinds(result) -> dict[str, str]:
    return {e["file"]: e["detail"]["kind"] for e in result.entries}


def test_modules_behind_a_tested_public_entry_count_as_covered(tmp_path):
    files, graph, zone_map = _project(
        tmp_path, '{"name": "pkg", "main": "src/index.ts"}'
    )
    assert public_entry_files(set(files.values())) == {files["index"]}

    result = run_test_coverage(graph, zone_map, "typescript")
    kinds = _kinds(result)
    assert files["core"] not in kinds
    assert files["helper"] not in kinds
    # Reached only through a tested internal module: still transitive.
    assert kinds[files["internal"]] == "transitive_only"
    assert result.scored_files == 5


def test_without_a_public_entry_imports_stay_transitive(tmp_path):
    files, graph, zone_map = _project(tmp_path, '{"name": "pkg"}')
    kinds = _kinds(run_test_coverage(graph, zone_map, "typescript"))
    assert kinds[files["core"]] == "transitive_only"
    assert kinds[files["helper"]] == "transitive_only"


def test_loc_weighted_dimension_labels(capsys):
    test_health = {
        "score": 40.0,
        "strict": 40.0,
        "checks": 300,
        "failing": 7,
        "detectors": {"test_coverage": {"potential": 300, "weighted_failures": 180.4}},
    }
    assert is_loc_weighted_dimension(test_health)
    assert not is_loc_weighted_dimension({"detectors": {"smells": {}}})

    class _Dim:
        name = "Test health"

    render_dimension_context(
        "test_coverage",
        {"Test health": test_health},
        colorize_fn=lambda text, _style: text,
        get_dimension_for_detector_fn=lambda _detector: _Dim(),
    )
    out = capsys.readouterr().out
    assert "7 issues; 180 of 300 √LOC weight failing" in out
    assert "checks failing" not in out
