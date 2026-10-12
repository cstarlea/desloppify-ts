"""Smells marked ``skip_in_tests`` in the catalog report nothing in test files."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.languages.typescript.detectors.smells import (
    TS_SMELL_CHECKS,
    detect_smells,
)

_TEST_IDIOMS = (
    "const base = 'https://app.example.com/login';\n"
    "const team = { color: '#3366CC' };\n"
    "if (elapsed > 86401) done();\n"
    "const name = user!.name;\n"
    "const db = mock as unknown as Database;\n"
    "const xs = [...tally.values()].sort();\n"
    "// @ts-expect-error\n"
    "f(1);\n"
    "try { f(); } catch (e) { }\n"
)
_SKIPPED = {
    "hardcoded_url",
    "hardcoded_color",
    "magic_number",
    "non_null_assert",
    "double_cast",
    "sort_no_comparator",
    "ts_expect_error_undocumented",
}


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


@pytest.fixture(params=["tree", "fallback"])
def mode(request, monkeypatch):
    if request.param == "tree" and not importlib.util.find_spec(
        "tree_sitter_language_pack"
    ):
        pytest.skip("needs tree-sitter")
    if request.param == "fallback":
        monkeypatch.setattr(tree_mod, "get_parser", lambda grammar: None)
    return request.param


def _files_by_smell(tmp_path: Path) -> dict[str, set[str]]:
    entries, _ = detect_smells(tmp_path)
    return {e["id"]: {m["file"] for m in e["matches"]} for e in entries}


def test_catalog_flags_the_test_idiom_smells():
    flagged = {check["id"] for check in TS_SMELL_CHECKS if check.get("skip_in_tests")}
    assert flagged == _SKIPPED


@pytest.mark.parametrize(
    "test_file", ["src/team.test.ts", "src/__tests__/team.ts", "e2e/team.spec.ts"]
)
def test_test_idiom_smells_skip_test_files(tmp_path, mode, test_file):
    for name in ("src/team.ts", test_file):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(_TEST_IDIOMS)

    found = _files_by_smell(tmp_path)

    for smell in _SKIPPED:
        assert found.get(smell) == {"src/team.ts"}, smell
    # Smells without the flag still fire in tests.
    assert found["empty_catch"] == {"src/team.ts", test_file}
