"""switch_no_default skips switches the compiler already proves exhaustive."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from desloppify.languages.typescript.detectors.smells import detect_smells

HAS_TREESITTER = importlib.util.find_spec("tree_sitter_language_pack") is not None
needs_treesitter = pytest.mark.skipif(not HAS_TREESITTER, reason="needs tree-sitter")


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _switch_lines(tmp_path: Path, content: str) -> list[int]:
    (tmp_path / "a.ts").write_text(content)
    entries, _ = detect_smells(tmp_path)
    for entry in entries:
        if entry["id"] == "switch_no_default":
            return sorted(m["line"] for m in entry["matches"])
    return []


def test_plain_switch_without_default_fires(tmp_path):
    content = (
        "function f(s: S) {\n"
        "  switch (s) {\n"
        "    case 'a': go(); break\n"
        "    case 'b': stop(); break\n"
        "  }\n"
        "}\n"
    )
    assert _switch_lines(tmp_path, content) == [2]


@needs_treesitter
def test_typed_return_where_every_case_returns_is_skipped(tmp_path):
    content = (
        "export function color(s: Status): string {\n"
        "  switch (s) {\n"
        "    case 'a':\n"
        "    case 'b': {\n"
        "      return 'x'\n"
        "    }\n"
        "    case 'c': // note\n"
        "      throw new Error('c')\n"
        "  }\n"
        "}\n"
        "const g = (s: Status): string | null => {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b': return null\n"
        "  }\n"
        "}\n"
        "class K {\n"
        "  m(s: Status): Promise<number> {\n"
        "    switch (s) {\n"
        "      case 'a': return one()\n"
        "      case 'b': return two()\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    assert _switch_lines(tmp_path, content) == []


@needs_treesitter
def test_switch_followed_by_never_check_is_skipped(tmp_path):
    content = (
        "function f(s: S) {\n"
        "  switch (s) {\n"
        "    case 'a': go(); break\n"
        "    case 'b': stop(); break\n"
        "  }\n"
        "  assertNever(s)\n"
        "}\n"
        "function g(s: S) {\n"
        "  switch (s) {\n"
        "    case 'a': go(); break\n"
        "    case 'b': stop(); break\n"
        "  }\n"
        "  s satisfies never\n"
        "}\n"
        "function h(s: S): number {\n"
        "  switch (s) {\n"
        "    case 'a': return 1\n"
        "    case 'b': return 2\n"
        "  }\n"
        "  const unhandled: never = s\n"
        "  return unhandled\n"
        "}\n"
    )
    assert _switch_lines(tmp_path, content) == []


def test_default_with_never_check_is_silent(tmp_path):
    content = (
        "function f(s: S): string {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b': return 'y'\n"
        "    default: return assertNever(s)\n"
        "  }\n"
        "}\n"
        "function g(s: S) {\n"
        "  switch (s) {\n"
        "    case 'a': go(); break\n"
        "    case 'b': stop(); break\n"
        "    default: s satisfies never\n"
        "  }\n"
        "}\n"
    )
    assert _switch_lines(tmp_path, content) == []


@needs_treesitter
def test_switches_tsc_does_not_check_still_fire(tmp_path):
    content = (
        # 1: void function
        "function a(s: S): void {\n"
        "  switch (s) {\n"
        "    case 'a': return go()\n"
        "    case 'b': return stop()\n"
        "  }\n"
        "}\n"
        # 7: code after the switch
        "function b(s: S): string {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b': return 'y'\n"
        "  }\n"
        "  return 'z'\n"
        "}\n"
        # 14: a case falls through to further logic
        "function c(s: S): string {\n"
        "  switch (s) {\n"
        "    case 'a': log(); break\n"
        "    case 'b': return 'y'\n"
        "  }\n"
        "}\n"
        # 20: no return type
        "function d(s: S) {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b': return 'y'\n"
        "  }\n"
        "}\n"
        # 26: return type admits undefined
        "function e(s: S): string | undefined {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b': return 'y'\n"
        "  }\n"
        "}\n"
        # 32: last case is empty
        "function f(s: S): string {\n"
        "  switch (s) {\n"
        "    case 'a': return 'x'\n"
        "    case 'b':\n"
        "  }\n"
        "}\n"
    )
    assert _switch_lines(tmp_path, content) == [2, 8, 15, 21, 27, 33]
