"""The unused-params fixer renames syntax-tree nodes (roadmap 2.3, FX-9)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.params as params_mod
from desloppify.languages.typescript.fixers.params import (
    fix_unused_params,
    prefix_unused_params,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the unused-params fixer needs tree-sitter",
)


def _fix(source: str, *targets: tuple[str, int, int], path: str = "a.ts"):
    parsed = parse_text(source, path)
    entries = [{"name": name, "line": line, "col": col} for name, line, col in targets]
    out, fixed, skipped = prefix_unused_params(parsed, entries)
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["name"] for entry in fixed], skipped


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "targets", "expected"),
    [
        pytest.param(
            "function h(event: Event, ctx: C) { return ctx; }\n",
            [("event", 1, 12)],
            "function h(_event: Event, ctx: C) { return ctx; }\n",
            id="typed-param",
        ),
        pytest.param(
            "const f = x => 1;\n",
            [("x", 1, 11)],
            "const f = _x => 1;\n",
            id="bare-arrow-param",
        ),
        pytest.param(
            "f((a, b = 1, c?: number) => a);\n",
            [("b", 1, 7), ("c", 1, 14)],
            "f((a, _b = 1, _c?: number) => a);\n",
            id="default-and-optional",
        ),
        pytest.param(
            "f(({ c, d = 1, e: g }: T) => 0);\n",
            [("c", 1, 6), ("d", 1, 9), ("g", 1, 19)],
            "f(({ c: _c, d: _d = 1, e: _g }: T) => 0);\n",
            id="object-pattern-keeps-property-names",
        ),
        pytest.param(
            "f(([i, j], ...rest) => j);\n",
            [("i", 1, 5), ("rest", 1, 15)],
            "f(([_i, j], ..._rest) => j);\n",
            id="array-and-rest",
        ),
        pytest.param(
            "try {} catch (e) {}\ntry {} catch ({ message }) {}\n",
            [("e", 1, 15), ("message", 2, 17)],
            "try {} catch (_e) {}\ntry {} catch ({ message: _message }) {}\n",
            id="catch-bindings",
        ),
        pytest.param(
            "f((e) => items.map((e) => e.id));\n",
            [("e", 1, 4)],
            "f((_e) => items.map((e) => e.id));\n",
            id="shadowed-in-body",
        ),
        pytest.param(
            "class A { m(x: number) {} }\n",
            [("x", 1, 13)],
            "class A { m(_x: number) {} }\n",
            id="method",
        ),
        pytest.param(
            "function h(\n  a: string,\n  b: number,\n) { return a; }\n",
            [("b", 3, 3)],
            "function h(\n  a: string,\n  _b: number,\n) { return a; }\n",
            id="multiline-params",
        ),
    ],
)
def test_prefix_unused_params(source, targets, expected):
    text, _fixed, skipped = _fix(source, *targets)
    assert text == expected
    assert skipped == []


@needs_treesitter
def test_jsx_component_props():
    text, fixed, _skipped = _fix(
        "const C = ({ title, onClose }: Props) => <div>{title}</div>;\n",
        ("onClose", 1, 21),
        path="c.tsx",
    )
    assert fixed == ["onClose"]
    assert text == "const C = ({ title, onClose: _onClose }: Props) => <div>{title}</div>;\n"


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "target", "reason"),
    [
        pytest.param("const v = 1;\n", ("v", 1, 7), "not_a_parameter", id="variable"),
        pytest.param("function k<T>(a: T) {}\n", ("T", 1, 12), "not_a_parameter", id="type-param"),
        pytest.param(
            "f((a = g()) => 0);\n", ("g", 1, 8), "not_a_parameter", id="name-in-default-value"
        ),
        pytest.param(
            "class C { constructor(private x: number) {} }\n",
            ("x", 1, 31),
            "parameter_property",
            id="parameter-property",
        ),
        pytest.param(
            "const _a = 1;\nfunction k(a) { return _a; }\n",
            ("a", 2, 12),
            "name_taken",
            id="would-shadow-outer-name",
        ),
        pytest.param(
            "function k(a, _a) { return _a; }\n", ("a", 1, 12), "name_taken", id="would-collide"
        ),
        pytest.param(
            "function isA(x: unknown): x is A { return true; }\n",
            ("x", 1, 14),
            "used_in_signature",
            id="type-predicate",
        ),
        pytest.param(
            "function k(x: unknown): asserts x {}\n",
            ("x", 1, 12),
            "used_in_signature",
            id="asserts",
        ),
        pytest.param(
            "function k(a: number, b: typeof a) { return b; }\n",
            ("a", 1, 12),
            "used_in_signature",
            id="typeof-in-later-param",
        ),
        pytest.param("function k(_a) {}\n", ("_a", 1, 12), "not_found", id="already-prefixed"),
        pytest.param("function k(a) {}\n", ("zzz", 1, 12), "not_found", id="stale"),
    ],
)
def test_skips(source, target, reason):
    text, fixed, skipped = _fix(source, target)
    assert text == source
    assert fixed == []
    assert skipped == [reason]


@needs_treesitter
def test_fix_unused_params_writes_and_reports(tmp_path):
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("export function h(a: number, b: number) {}\nconst v = 1;\n")

    result = fix_unused_params(
        [
            {"file": str(ts_file), "name": "a", "line": 1, "col": 19, "issue_id": "u::a.ts::a:1"},
            {"file": str(ts_file), "name": "v", "line": 2, "col": 7},
        ]
    )

    assert ts_file.read_text() == "export function h(_a: number, b: number) {}\nconst v = 1;\n"
    [entry] = result.entries
    assert entry["fixed_issue_ids"] == ["u::a.ts::a:1"]
    assert result.skip_reasons == {"not_a_parameter": 1}


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(params_mod, "get_parser", lambda _grammar: None)
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("function h(a) {}\n")

    result = fix_unused_params([{"file": str(ts_file), "name": "a", "line": 1}])

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert ts_file.read_text() == "function h(a) {}\n"
    assert "needs tree-sitter" in capsys.readouterr().err
