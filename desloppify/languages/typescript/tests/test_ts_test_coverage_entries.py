"""Coverage through a tested public entry or directory index, served assets,
thin modules, and the √LOC-weighted check labels."""

from __future__ import annotations

from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.app.commands.next.render_scoring import render_dimension_context
from desloppify.engine.detectors.test_coverage.detector import run_test_coverage
from desloppify.engine.policy.zones import FileZoneMap, Zone, ZoneRule
from desloppify.engine.scoring import is_loc_weighted_dimension
from desloppify.languages.typescript.detectors.deps.imports import ImportExtractor
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.test_coverage import (
    has_testable_logic,
    is_integration_test,
    is_served_asset,
    public_entry_files,
)

_LOGIC = (
    "export function run(n: number): number {\n"
    + "  n += 1;\n" * 10
    + "  return n;\n}\n"
)


needs_treesitter = pytest.mark.skipif(
    not ImportExtractor().uses_treesitter,
    reason="needs tree-sitter with the tsx grammar",
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


def _router_project(root: Path, test_name: str) -> tuple[dict, dict, FileZoneMap]:
    """A test named for routers/index.ts; the index merges core and members,
    core uses helpers."""
    files = {
        "index": _touch(
            root,
            "src/routers/index.ts",
            "import { run as c } from './core';\nimport { run as m } from './members';\n"
            + _LOGIC.replace("n += 1", "n += c(n) + m(n)"),
        ),
        "core": _touch(
            root,
            "src/routers/core.ts",
            "import { run as h } from './helpers';\n" + _LOGIC,
        ),
        "members": _touch(root, "src/routers/members.ts", _LOGIC),
        "helpers": _touch(root, "src/routers/helpers.ts", _LOGIC),
        "shared": _touch(root, "src/shared.ts", _LOGIC),
        "test": _touch(
            root,
            f"src/routers/{test_name}",
            "import { caller } from '../../test/caller';\n"
            "it('runs', () => { expect(caller.run(1)).toBe(12); });\n",
        ),
    }
    graph = {
        files["index"]: {
            "imports": {files["core"], files["members"], files["shared"]},
            "importer_count": 1,
        },
        files["core"]: {"imports": {files["helpers"]}, "importer_count": 1},
        files["members"]: {"imports": set(), "importer_count": 1},
        files["helpers"]: {"imports": set(), "importer_count": 1},
        files["shared"]: {"imports": set(), "importer_count": 1},
        files["test"]: {"imports": set()},
    }
    zone_map = FileZoneMap(sorted(files.values()), [ZoneRule(Zone.TEST, [".test."])])
    return files, graph, zone_map


def test_integration_test_of_an_index_covers_the_modules_it_composes(tmp_path):
    files, graph, zone_map = _router_project(tmp_path, "index.integration.test.ts")
    kinds = _kinds(run_test_coverage(graph, zone_map, "typescript"))
    assert files["index"] not in kinds
    assert files["core"] not in kinds
    assert files["members"] not in kinds
    # Behind a composed module, or outside the index's directory: still transitive.
    assert kinds[files["helpers"]] == "transitive_only"
    assert kinds[files["shared"]] == "transitive_only"


def test_unit_test_of_an_index_leaves_its_imports_transitive(tmp_path):
    files, graph, zone_map = _router_project(tmp_path, "index.test.ts")
    kinds = _kinds(run_test_coverage(graph, zone_map, "typescript"))
    assert files["index"] not in kinds
    assert kinds[files["core"]] == "transitive_only"
    assert kinds[files["members"]] == "transitive_only"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/a/index.integration.test.ts", True),
        ("src/a/widget.rbac.integration.test.ts", True),
        ("e2e/login.spec.ts", True),
        ("tests/integration/a.test.ts", True),
        ("src/a/index.test.ts", False),
        ("src/integrations/slack.test.ts", False),
    ],
)
def test_is_integration_test(path, expected):
    assert is_integration_test(path) is expected


def test_files_in_a_framework_static_dir_are_not_scored(tmp_path):
    _touch(tmp_path, "package.json", '{"dependencies": {"next": "15.0.0"}}')
    _touch(tmp_path, "next.config.ts", "export default {};\n")
    sw = "self.addEventListener('fetch', (e) => {\n" + "  e.x += 1;\n" * 12 + "});\n"
    files = [
        _touch(tmp_path, "public/sw.js", sw),
        _touch(tmp_path, "src/sw.ts", sw),
    ]
    assert is_served_asset("public/sw.js")
    assert not is_served_asset("src/sw.ts")
    graph = {f: {"imports": set(), "importer_count": 0} for f in files}
    zone_map = FileZoneMap(files, [ZoneRule(Zone.TEST, [".test."])])
    result = run_test_coverage(graph, zone_map, "typescript")
    assert set(_kinds(result)) == {"src/sw.ts"}
    assert result.scored_files == 1


def test_public_dir_without_a_framework_is_still_scored(tmp_path):
    _touch(tmp_path, "package.json", '{"name": "lib"}')
    _touch(tmp_path, "public/sw.js", _LOGIC)
    assert not is_served_asset("public/sw.js")


@pytest.mark.parametrize(
    "source",
    [
        # A Next page that only names its client component and its metadata.
        "import type { Metadata } from 'next'\n"
        "import { Page } from './page-client'\n"
        "export const metadata: Metadata = {\n"
        "  title: 'Export',\n  robots: { index: false, follow: false },\n}\n"
        "export default Page\n",
        "export const COLORS = { bg: '#000', fg: `${BASE}` } as const;\n"
        "const SIZES = [1, -2, 3];\nexport { SIZES };\n",
        "export const { GET, POST } = handlers;\n",
        "export const config = { runtime: 'edge', ...base } satisfies Config;\n",
    ],
)
@needs_treesitter
def test_data_only_modules_have_no_testable_logic(source):
    assert not has_testable_logic("src/page.tsx", source)


@pytest.mark.parametrize(
    "source",
    [
        "export const metadata = { title: makeTitle('Export') };\n",
        "export const flag = process.env.X ? 'a' : 'b';\n",
        "export const handler = () => 1;\n",
        "export default function Page() { return null }\n",
        "export default wrap(Page)\n",
    ],
)
def test_calls_and_functions_are_testable_logic(source):
    assert has_testable_logic("src/page.tsx", source)


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
