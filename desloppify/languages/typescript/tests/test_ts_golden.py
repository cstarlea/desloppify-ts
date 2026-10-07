"""Golden end-to-end scans over small, realistic TypeScript projects.

Each project under ``golden/projects`` is scanned with every mechanical
detector and compared against ``golden/snapshots/<name>.json``. Intent checks
in ``golden/expectations.json`` say which findings must (not) appear and which
known false positives / negatives are still outstanding.

Two layers:

* hermetic (always): no Node toolchain, so tsc/knip report reduced coverage.
* node (opt-in): real, pinned tsc and knip. Install them once with
  ``npm ci --prefix desloppify/languages/typescript/tests/golden/node``;
  the tests skip when they are missing. Snapshots: ``<name>.node.json``.

After an intended behavior change, regenerate the snapshots and review the
diff like any other code change:

    DESLOPPIFY_UPDATE_GOLDEN=1 pytest desloppify/languages/typescript/tests/test_ts_golden.py
"""

from __future__ import annotations

import difflib
import json
import os

import pytest

from desloppify.languages._framework.treesitter import is_available
from desloppify.languages.typescript.tests.golden import harness


def _tsx_grammar_loads() -> bool:
    if not is_available():
        return False
    from desloppify.languages._framework.treesitter.analysis.extractors import _get_parser

    try:
        _get_parser("tsx")
    except Exception:  # noqa: BLE001 - any load failure means "can't run here"
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _tsx_grammar_loads(),
    reason="golden scans need tree-sitter with the tsx grammar (pip install 'desloppify[full]')",
)

EXPECTATIONS = json.loads((harness.GOLDEN_DIR / "expectations.json").read_text())
PROJECTS = harness.project_names()
_NODE_HINT = "npm ci --prefix desloppify/languages/typescript/tests/golden/node"
if os.environ.get("DESLOPPIFY_REQUIRE_NODE_GOLDEN") and not harness.node_tools_installed():
    raise RuntimeError(f"DESLOPPIFY_REQUIRE_NODE_GOLDEN is set but tsc/knip are missing: {_NODE_HINT}")
needs_node = pytest.mark.skipif(
    not harness.node_tools_installed(),
    reason=f"pinned tsc/knip not installed ({_NODE_HINT})",
)
LAYERS = [
    pytest.param(False, id="hermetic"),
    pytest.param(True, id="node", marks=needs_node),
]


@pytest.fixture(scope="module")
def scans(tmp_path_factory):
    cache: dict[tuple[str, bool], dict] = {}

    def get(name: str, node_tools: bool) -> dict:
        key = (name, node_tools)
        if key not in cache:
            root = tmp_path_factory.mktemp("golden-node" if node_tools else "golden")
            project = harness.copy_project(name, root, node_tools=node_tools)
            cache[key] = harness.scan_project(project, node_tools=node_tools)
        return cache[key]

    return get


def _matching(ids: list[str], prefix: str) -> list[str]:
    return [issue_id for issue_id in ids if issue_id.startswith(prefix)]


def test_every_project_has_snapshot_and_expectations():
    hermetic = {
        p.name.removesuffix(".json")
        for p in harness.SNAPSHOTS_DIR.glob("*.json")
        if not p.name.endswith(".node.json")
    }
    assert hermetic == set(PROJECTS) or os.environ.get(harness.UPDATE_ENV)
    assert set(EXPECTATIONS) - {"_doc"} == set(PROJECTS)


@pytest.mark.parametrize("node_tools", LAYERS)
@pytest.mark.parametrize("name", PROJECTS)
def test_snapshot(name, node_tools, scans):
    actual = harness.dump_snapshot(scans(name, node_tools))
    path = harness.snapshot_path(name, node_tools=node_tools)
    if os.environ.get(harness.UPDATE_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
        return
    assert path.exists(), f"missing snapshot {path.name}; run with {harness.UPDATE_ENV}=1"
    expected = path.read_text()
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"snapshots/{path.name}",
                tofile="current scan",
            )
        )
        pytest.fail(
            f"{path.name}: scan output changed. If intended, rerun with "
            f"{harness.UPDATE_ENV}=1 and review the diff.\n{diff}"
        )


@pytest.mark.parametrize("node_tools", LAYERS)
@pytest.mark.parametrize("name", PROJECTS)
def test_expectations(name, node_tools, scans):
    spec = EXPECTATIONS[name].get("node", {}) if node_tools else EXPECTATIONS[name]
    ids = [issue["id"] for issue in scans(name, node_tools)["issues"]]
    problems = []
    for prefix in spec.get("must_find", []):
        if not _matching(ids, prefix):
            problems.append(f"missing expected finding: {prefix}")
    for prefix in spec.get("must_not_find", []):
        for issue_id in _matching(ids, prefix):
            problems.append(f"unexpected finding: {issue_id}")
    for prefix, why in spec.get("known_false_positives", {}).items():
        if not _matching(ids, prefix):
            problems.append(
                f"known false positive no longer reported: {prefix} ({why}). "
                "Move it to must_not_find."
            )
    for prefix, why in spec.get("known_false_negatives", {}).items():
        if _matching(ids, prefix):
            problems.append(
                f"known false negative is now detected: {prefix} ({why}). Move it to must_find."
            )
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("node_tools", LAYERS)
@pytest.mark.parametrize("name", PROJECTS)
def test_scan_does_not_depend_on_cwd(tmp_path, name, node_tools):
    """Roadmap 1.9: the process cwd must not change findings, IDs or potentials."""
    project = harness.copy_project(name, tmp_path, node_tools=node_tools)
    from_inside = harness.scan_project(project, node_tools=node_tools)
    from_parent = harness.scan_project(project, cwd=tmp_path, node_tools=node_tools)
    assert from_parent == from_inside
