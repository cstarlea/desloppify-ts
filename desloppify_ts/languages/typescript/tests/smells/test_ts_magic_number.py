"""magic_number skips named constants and unit factors, and still finds inline literals."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import desloppify_ts.languages.typescript.syntax.tree as tree_mod
from desloppify_ts.languages.typescript.detectors.smells import detect_smells

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


def _magic_lines(tmp_path: Path, content: str) -> list[int]:
    (tmp_path / "a.ts").write_text(content)
    entries, _ = detect_smells(tmp_path)
    for entry in entries:
        if entry["id"] == "magic_number":
            return sorted(m["line"] for m in entry["matches"])
    return []


def test_inline_literals_still_fire(tmp_path, mode):
    content = (
        "if (count >= 9999) run();\n"
        "const total = price * 1500;\n"
        "if (parsed.getFullYear() < 2020) fail();\n"
        "const until = Date.now() + 5000;\n"
        "if (size > 1024) shrink();\n"
    )
    assert _magic_lines(tmp_path, content) == [1, 2, 3, 4, 5]


def test_upper_snake_constant_initialisers_are_skipped(tmp_path, mode):
    content = (
        "const DAY_MS = 24 * 60 * 60 * 1000\n"
        "export const MAX_SCORE = BASE + 2500\n"
        "const BURST_LIMIT = { limit: 60, windowMs: 5 * 1500 }\n"
        "const lower = BASE + 2500\n"
    )
    assert _magic_lines(tmp_path, content) == [4]


@needs_treesitter
def test_multi_line_constant_object_is_skipped_but_not_its_functions(tmp_path):
    content = (
        "export const LIMITS = {\n"
        "  burst: { limit: 10, windowMs: 2500 * 4 },\n"
        "  check: (n: number) => n > 2500,\n"
        "} as const\n"
        "function f() {\n"
        "  const THRESHOLD = 7500 + offset\n"
        "  return x > 7500\n"
        "}\n"
    )
    assert _magic_lines(tmp_path, content) == [3, 7]


def test_unit_factors_in_arithmetic_are_skipped(tmp_path, mode):
    content = (
        "const ttl = days * 24 * 60 * 60 * 1000\n"
        "const chunk = new Uint8Array(64 * 1024)\n"
        "const expired = new Date(Date.now() - 1000)\n"
        "const seconds = Math.floor(ms / 1000)\n"
        "const big = 8 * 1048576\n"
        "const week = n * 604800000\n"
        "const mixed = a * 1000 + b * 4500\n"
    )
    assert _magic_lines(tmp_path, content) == [7]


def test_unit_factor_in_comparison_still_fires(tmp_path, mode):
    assert _magic_lines(tmp_path, "if (elapsed === 3600) stop();\n") == [1]
