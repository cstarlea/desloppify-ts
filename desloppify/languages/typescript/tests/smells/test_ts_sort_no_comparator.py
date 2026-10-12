"""sort_no_comparator skips sorts that plainly sort strings, and test files."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.languages.typescript.detectors.smells import detect_smells

HAS_TREESITTER = importlib.util.find_spec("tree_sitter_language_pack") is not None
needs_treesitter = pytest.mark.skipif(not HAS_TREESITTER, reason="needs tree-sitter")


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


@pytest.fixture(params=["tree", "fallback"])
def mode(request, monkeypatch):
    if request.param == "tree" and not HAS_TREESITTER:
        pytest.skip("needs tree-sitter")
    if request.param == "fallback":
        monkeypatch.setattr(tree_mod, "get_parser", lambda grammar: None)
    return request.param


def _sort_lines(tmp_path: Path, content: str, name: str = "a.ts") -> list[int]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    entries, _ = detect_smells(tmp_path)
    for entry in entries:
        if entry["id"] == "sort_no_comparator":
            return sorted(m["line"] for m in entry["matches"])
    return []


def test_number_arrays_still_fire(tmp_path, mode):
    content = (
        "const totals = [...tally.values()].sort()\n"
        "const sorted = scores.sort()\n"
        "const ages = people.map((p) => p.age).sort()\n"
        "const xs = [3, 1, 2].sort()\n"
    )
    assert _sort_lines(tmp_path, content) == [1, 2, 3, 4]


def test_joined_sorts_are_skipped(tmp_path, mode):
    content = (
        "const a = [...unset].sort().join(', ')\n"
        "const b = ownerIds.slice().sort().join(',')\n"
    )
    assert _sort_lines(tmp_path, content) == []


def test_sorts_inside_expect_are_skipped(tmp_path, mode):
    content = (
        "expect(result.sort()).toEqual(expected.sort())\n"
        "expect(rows.map((r) => r.score).sort()).toEqual([1, 2])\n"
    )
    assert _sort_lines(tmp_path, content, "src/check.ts") == []


@needs_treesitter
def test_sort_inside_multi_line_expect_is_skipped(tmp_path):
    content = "expect(\n  values\n    .sort(),\n).toEqual(\n  others.sort(),\n)\n"
    assert _sort_lines(tmp_path, content) == []


def test_string_field_maps_and_key_lists_are_skipped(tmp_path, mode):
    content = (
        "const ids = rows.map((r) => r.groupId).sort()\n"
        "const names = groups.map((g) => g.name).sort()\n"
        "const keys = Object.keys(config).sort()\n"
        "const parts = header.split(',').sort()\n"
    )
    assert _sort_lines(tmp_path, content) == []


@needs_treesitter
def test_obviously_string_receivers_are_skipped(tmp_path):
    content = (
        "const a = ['b', 'a', `c`].sort()\n"
        "const tags: string[] = load()\n"
        "const b = tags.sort()\n"
        "function f(names: readonly string[] | undefined, n: number[]) {\n"
        "  return [names!.slice().sort(), n.sort()]\n"
        "}\n"
        "const set = new Set<string>()\n"
        "const c = [...set].sort()\n"
        "const d = [first.id, other.id].sort()\n"
        "const e = queuedIds.sort()\n"
        "const entries = Object.entries(map).sort()\n"
        "const f2 = rows.map((r) => r.id).filter(Boolean).sort()\n"
    )
    assert _sort_lines(tmp_path, content) == [5]


@needs_treesitter
def test_typed_number_array_named_like_ids_still_fires(tmp_path):
    content = "const userIds: number[] = load()\nconst x = userIds.sort()\n"
    assert _sort_lines(tmp_path, content) == [2]


def test_test_files_are_skipped(tmp_path, mode):
    content = "const totals = [...tally.values()].sort()\n"
    assert _sort_lines(tmp_path, content, "src/report.test.ts") == []
