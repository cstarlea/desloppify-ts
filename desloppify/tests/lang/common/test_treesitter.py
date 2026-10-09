"""Tests for tree-sitter parsing, the parse cache, and responsibility cohesion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desloppify.languages._framework.treesitter as ts_mod
from desloppify.base.runtime_state import make_runtime_context, runtime_scope
from desloppify.languages._framework.treesitter import TYPESCRIPT_SPEC, is_available
from desloppify.languages._framework.treesitter.cache import (
    current_parse_tree_cache,
    disable_parse_cache,
    enable_parse_cache,
)
from desloppify.languages._framework.treesitter.cohesion import (
    detect_responsibility_cohesion,
    make_cohesion_phase,
)
from desloppify.languages._framework.treesitter.parsing import _get_parser

needs_treesitter = pytest.mark.skipif(
    not is_available(), reason="tree-sitter-language-pack not installed"
)


@pytest.fixture
def ts_file(tmp_path: Path) -> str:
    f = tmp_path / "sample.ts"
    f.write_text("export function add(a: number, b: number) {\n  return a + b;\n}\n")
    return str(f)


def _write(tmp_path: Path, name: str, code: str) -> str:
    f = tmp_path / name
    f.write_text(code)
    return str(f)


def test_is_available_false_when_flag_cleared(monkeypatch) -> None:
    monkeypatch.setattr(ts_mod, "_AVAILABLE", False)
    assert ts_mod.is_available() is False


@needs_treesitter
class TestParseTreeCache:
    def test_cache_hit(self, ts_file):
        parser, _language = _get_parser("tsx")
        with runtime_scope(make_runtime_context()):
            enable_parse_cache()
            try:
                cache = current_parse_tree_cache()
                result1 = cache.get_or_parse(ts_file, parser, "tsx")
                result2 = cache.get_or_parse(ts_file, parser, "tsx")
                assert result1 is not None
                assert result2 is not None
                assert result1[1] is result2[1]
            finally:
                disable_parse_cache()

    def test_cache_disabled(self, ts_file):
        parser, _language = _get_parser("tsx")
        with runtime_scope(make_runtime_context()):
            disable_parse_cache()
            cache = current_parse_tree_cache()
            result1 = cache.get_or_parse(ts_file, parser, "tsx")
            result2 = cache.get_or_parse(ts_file, parser, "tsx")
            assert result1 is not None
            assert result2 is not None
            assert result1[1] is not result2[1]

    def test_cache_cleanup(self):
        with runtime_scope(make_runtime_context()):
            enable_parse_cache()
            cache = current_parse_tree_cache()
            assert cache._enabled
            disable_parse_cache()
            assert not cache._enabled
            assert cache._trees == {}


@needs_treesitter
class TestResponsibilityCohesion:
    def test_cohesive_file_no_flags(self, tmp_path):
        code = ""
        for i in range(10):
            next_fn = f"fn{i + 1}" if i < 9 else "fn0"
            code += f"function fn{i}() {{\n  {next_fn}();\n  const x = {i};\n  return x;\n}}\n\n"
        path = _write(tmp_path, "cohesive.ts", code)

        entries, checked = detect_responsibility_cohesion([path], TYPESCRIPT_SPEC, min_loc=5)

        assert entries == []
        assert checked == 1

    def test_disconnected_singletons_not_flagged(self, tmp_path):
        code = ""
        for i in range(10):
            code += f"function isolated{i}() {{\n  const x = {i};\n  const y = {i * 2};\n  return x + y;\n}}\n\n"
        path = _write(tmp_path, "toolkit.ts", code)

        entries, checked = detect_responsibility_cohesion([path], TYPESCRIPT_SPEC, min_loc=5)

        assert entries == []
        assert checked == 1

    def test_mixed_responsibilities_flagged(self, tmp_path):
        groups = [("auth", ["Login", "Validate", "Hash"]),
                  ("db", ["Connect", "Query", "Parse"]),
                  ("http", ["Serve", "Route", "Respond"])]
        code = ""
        for prefix, names in groups:
            for name, nxt in zip(names, [*names[1:], None], strict=True):
                call = f"{prefix}{nxt}();" if nxt else "return 1;"
                code += f"function {prefix}{name}() {{ {call} }}\n"
            code += "\n"
        code += "const utilA = () => { return 1; };\nconst utilB = () => { return 2; };\n"
        path = _write(tmp_path, "mixed.ts", code)

        entries, checked = detect_responsibility_cohesion([path], TYPESCRIPT_SPEC, min_loc=5)

        assert len(entries) == 1
        assert entries[0]["component_count"] >= 5
        assert checked == 1


def test_make_cohesion_phase_label() -> None:
    assert make_cohesion_phase(TYPESCRIPT_SPEC).label == "Responsibility cohesion"


def test_make_cohesion_phase_run_with_entries(tmp_path) -> None:
    phase = make_cohesion_phase(TYPESCRIPT_SPEC)
    lang = SimpleNamespace(file_finder=lambda _path: ["src/big.ts"])
    entry = {
        "file": "src/big.ts",
        "component_count": 6,
        "function_count": 14,
        "families": ["auth", "db", "http"],
    }
    with patch(
        "desloppify.languages._framework.treesitter.cohesion.detect_responsibility_cohesion",
        return_value=([entry], 1),
    ):
        issues, potentials = phase.run(tmp_path, lang)

    assert potentials["responsibility_cohesion"] == 1
    assert issues[0]["detector"] == "responsibility_cohesion"
    assert issues[0]["detail"]["families"] == ["auth", "db", "http"]
