"""Unused findings are categorised on the syntax tree (roadmap 2.13, FX-15)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.unused as unused_mod
from desloppify.languages.typescript.detectors.unused import (
    _categorize_entries,
    detect_unused,
)

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="syntax-tree categories need tree-sitter",
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _entry(path: Path, source: str, name: str, line: int, nth: int = 0) -> dict:
    """An entry for the ``nth`` occurrence of ``name`` on ``line`` (tsc's 1-based col)."""
    text = source.splitlines()[line - 1]
    match = list(re.finditer(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text))[nth]
    return {"file": str(path), "line": line, "col": match.start() + 1, "name": name}


def _categorize(tmp_path: Path, source: str, name: str, line: int, nth: int = 0, path="a.ts"):
    file = tmp_path / path
    file.write_text(source)
    entry = _entry(file, source, name, line, nth)
    _categorize_entries([entry])
    return entry["category"]


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "name", "line", "nth"),
    [
        pytest.param("import d from './m';\n", "d", 1, 0, id="default"),
        pytest.param("import { a, b } from './m';\n", "b", 1, 0, id="named"),
        pytest.param("import { a as b } from './m';\n", "b", 1, 0, id="aliased"),
        pytest.param(
            "import {\n  getCart,\n  ShopifyCart,\n  Other\n} from './types';\n",
            "ShopifyCart",
            3,
            0,
            id="multi-line-specifier",
        ),
        pytest.param(
            "import type {\n  A,\n  B,\n} from './types';\n", "B", 3, 0, id="multi-line-type"
        ),
        pytest.param("import * as ns from './m';\n", "ns", 1, 0, id="namespace"),
        pytest.param("import type { T } from './m';\n", "T", 1, 0, id="type"),
        pytest.param("import { type T, u } from './m';\n", "T", 1, 0, id="inline-type"),
        pytest.param("import fs = require('fs');\n", "fs", 1, 0, id="require"),
        pytest.param("namespace N { export const x = 1; }\nimport x = N.x;\n", "x", 2, 0, id="alias"),
    ],
)
def test_imports(tmp_path, source, name, line, nth):
    assert _categorize(tmp_path, source, name, line, nth) == "imports"


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "name", "line", "nth", "path"),
    [
        pytest.param("function f(a: number) {}\n", "a", 1, 0, "a.ts", id="plain"),
        pytest.param("function f(a?: number) {}\n", "a", 1, 0, "a.ts", id="optional"),
        pytest.param("function f(a = 1) {}\n", "a", 1, 0, "a.ts", id="default"),
        pytest.param("function f({ a, b }: P) { b; }\n", "a", 1, 0, "a.ts", id="object-pattern"),
        pytest.param("function f({ a: x }: P) {}\n", "x", 1, 0, "a.ts", id="renamed-pattern"),
        pytest.param("function f([a, b]: T) { b; }\n", "a", 1, 0, "a.ts", id="array-pattern"),
        pytest.param("function f(...rest: T[]) {}\n", "rest", 1, 0, "a.ts", id="rest"),
        pytest.param("const f = (a, b) => b;\n", "a", 1, 0, "a.js", id="arrow"),
        pytest.param("const f = a => 1;\n", "a", 1, 0, "a.js", id="bare-arrow"),
        pytest.param("class C { m(a: number) {} }\n", "a", 1, 0, "a.ts", id="method"),
        pytest.param("class C { constructor(private a: number) {} }\n", "a", 1, 0, "a.ts", id="parameter-property"),
        pytest.param("const o = { m(a) {} };\n", "a", 1, 0, "a.js", id="object-method"),
        pytest.param(
            "function f(\n  a: number,\n  b: number,\n) { return a; }\n", "b", 3, 0, "a.ts", id="multi-line"
        ),
        pytest.param("try {} catch (e) {}\n", "e", 1, 0, "a.ts", id="catch"),
        pytest.param("try {} catch ({ message }) {}\n", "message", 1, 0, "a.ts", id="catch-pattern"),
    ],
)
def test_params(tmp_path, source, name, line, nth, path):
    assert _categorize(tmp_path, source, name, line, nth, path) == "params"


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "name", "line", "nth"),
    [
        pytest.param("const a = 1;\n", "a", 1, 0, id="const"),
        pytest.param("export {};\nfunction f() { let a = 1; }\n", "a", 2, 0, id="local"),
        pytest.param("const { a, b } = o;\nb;\n", "a", 1, 0, id="destructured-local"),
        pytest.param("function g() {}\n", "g", 1, 0, id="function"),
        pytest.param("type T = number;\n", "T", 1, 0, id="type"),
        pytest.param("interface I {}\n", "I", 1, 0, id="interface"),
        pytest.param("class C { private x = 1; }\n", "x", 1, 0, id="private-member"),
        pytest.param("function f<T>() {}\n", "T", 1, 0, id="type-parameter"),
        pytest.param("function f(cb: (a: T) => void) { cb; }\n", "T", 1, 0, id="type-in-parameter"),
        pytest.param("function f(a = () => { const b = 1; }) { a; }\n", "b", 1, 0, id="local-in-default"),
    ],
)
def test_vars(tmp_path, source, name, line, nth):
    assert _categorize(tmp_path, source, name, line, nth) == "vars"


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "name", "col", "expected"),
    [
        pytest.param("import { a, b } from './m';\n", "(entire import)", 1, "imports", id="entire-import"),
        pytest.param("function f({ a, b }: P) {}\n", "(all destructured elements)", 12, "params", id="param-pattern"),
        pytest.param("try {} catch ({ a, b }) {}\n", "(all destructured elements)", 15, "params", id="catch-pattern"),
        pytest.param("const { a, b } = o;\n", "(all destructured elements)", 7, "vars", id="local-pattern"),
        pytest.param("let a, b;\n", "(all variables)", 1, "vars", id="all-variables"),
        pytest.param("function f<A, B>() {}\n", "(all type parameters)", 11, "vars", id="type-parameters"),
        # tsc reports a pattern's only element at the pattern, under its own name.
        pytest.param("function f({ children }: P) {}\n", "children", 12, "params", id="lone-param-element"),
        pytest.param("const f = ([a]) => 1;\n", "a", 12, "params", id="lone-arrow-element"),
        pytest.param("const { a } = o;\n", "a", 7, "vars", id="lone-local-element"),
    ],
)
def test_aggregates_and_patterns(tmp_path, source, name, col, expected):
    file = tmp_path / "a.ts"
    file.write_text(source)
    entry = {"file": str(file), "line": 1, "col": col, "name": name}
    _categorize_entries([entry])
    assert entry["category"] == expected


@needs_treesitter
def test_each_file_is_parsed_once(tmp_path, monkeypatch):
    source = "import { a } from './m';\nfunction f(b) { const c = 1; }\n"
    file = tmp_path / "a.ts"
    file.write_text(source)
    calls: list[str] = []
    real = unused_mod.parse_text

    def counting(text, path):
        calls.append(str(path))
        return real(text, path)

    monkeypatch.setattr(unused_mod, "parse_text", counting)
    entries = [_entry(file, source, "a", 1), _entry(file, source, "b", 2), _entry(file, source, "c", 2)]
    _categorize_entries(entries)
    assert calls == [str(file)]
    assert [e["category"] for e in entries] == ["imports", "params", "vars"]


@needs_treesitter
def test_name_not_in_tree_falls_back_to_line_heuristic(tmp_path):
    file = tmp_path / "a.ts"
    file.write_text("import { a } from './m';\n")
    entry = {"file": str(file), "line": 1, "col": 1, "name": "missing"}
    _categorize_entries([entry])
    assert entry["category"] == "imports"


def test_without_treesitter_uses_line_heuristic(tmp_path, monkeypatch):
    monkeypatch.setattr(unused_mod, "parse_text", lambda text, path: None)
    source = "import {\n  a,\n  b,\n} from './m';\nfunction f(x) {}\nconst y = 1;\n"
    file = tmp_path / "a.ts"
    file.write_text(source)
    entries = [
        _entry(file, source, "b", 3),
        _entry(file, source, "x", 5),
        _entry(file, source, "y", 6),
        {"file": str(file), "line": 1, "col": 1, "name": "(entire import)"},
    ]
    _categorize_entries(entries)
    assert [e["category"] for e in entries] == ["imports", "vars", "vars", "imports"]


def test_unreadable_file_is_vars(tmp_path):
    entry = {"file": str(tmp_path / "gone.ts"), "line": 1, "col": 1, "name": "a"}
    _categorize_entries([entry])
    assert entry["category"] == "vars"


@needs_treesitter
@pytest.mark.parametrize(
    ("category", "names"),
    [("imports", ["a"]), ("params", ["b"]), ("vars", ["c"]), ("all", ["a", "b", "c"])],
)
def test_detect_unused_filters_by_category(tmp_path, monkeypatch, category, names):
    (tmp_path / "tsconfig.json").write_text("{}\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.ts").write_text("import { a } from './m';\nfunction f(b) { const c = 1; }\n")

    class _Result:
        stdout = "\n".join(
            [
                "src/app.ts(1,10): error TS6133: 'a' is declared but its value is never read.",
                "src/app.ts(2,12): error TS6133: 'b' is declared but its value is never read.",
                "src/app.ts(2,23): error TS6133: 'c' is declared but its value is never read.",
            ]
        )
        stderr = ""
        returncode = 2

    monkeypatch.setattr(unused_mod, "find_ts_and_js_files", lambda _path: [str(src / "app.ts")])
    monkeypatch.setattr(unused_mod, "_run_tsc_unused_check", lambda *a, **k: _Result())
    entries, _total = detect_unused(tmp_path, category)
    assert [e["name"] for e in entries] == names
    assert all(category in ("all", e["category"]) for e in entries)


def test_fixers_detect_their_own_category(monkeypatch):
    import desloppify.languages.typescript._fixers as ts_fixers_mod

    seen: list[str] = []

    def fake_detect(path, category="all"):
        seen.append(category)
        return [], 0

    monkeypatch.setattr(ts_fixers_mod.unused_detector_mod, "detect_unused", fake_detect)
    fixers = ts_fixers_mod.get_ts_fixers()
    for name in ("unused-imports", "unused-vars", "unused-params"):
        fixers[name].detect(Path("."))
    assert seen == ["imports", "vars", "params"]
