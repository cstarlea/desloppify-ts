"""The unused-vars fixer edits syntax-tree ranges (roadmap 2.3, FX-7/FX-8)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.vars as vars_mod
from desloppify.languages.typescript.fixers.vars import (
    ALL_DESTRUCTURED,
    ALL_VARIABLES,
    fix_unused_vars,
    remove_unused_vars,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the unused-vars fixer needs tree-sitter",
)


def _fix(source: str, *targets: tuple[str, int, int], path: str = "a.ts"):
    parsed = parse_text(source, path)
    entries = [{"name": name, "line": line, "col": col} for name, line, col in targets]
    out, fixed, skipped = remove_unused_vars(parsed, entries)
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["name"] for entry in fixed], skipped


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "targets", "expected"),
    [
        pytest.param(
            "const a = 1;\nconst b = 2;\nuse(b);\n",
            [("a", 1, 7)],
            "const b = 2;\nuse(b);\n",
            id="whole-statement",
        ),
        pytest.param(
            "const a = 1, b = 2;\nuse(b);\n",
            [("a", 1, 7)],
            "const b = 2;\nuse(b);\n",
            id="first-declarator",
        ),
        pytest.param(
            "const a = 1, b = 2;\nuse(a);\n",
            [("b", 1, 14)],
            "const a = 1;\nuse(a);\n",
            id="last-declarator",
        ),
        pytest.param(
            "const { a, b: c, d = 1 } = o;\nuse(a);\n",
            [("c", 1, 15), ("d", 1, 18)],
            "const { a } = o;\nuse(a);\n",
            id="pattern-members",
        ),
        pytest.param(
            "const {\n  a,\n  b,\n  c,\n} = props;\nuse(a, c);\n",
            [("b", 3, 3)],
            "const {\n  a,\n  c,\n} = props;\nuse(a, c);\n",
            id="multiline-pattern",
        ),
        pytest.param(
            "const { a: { b, c } } = o;\nuse(c);\n",
            [("b", 1, 14)],
            "const { a: { c } } = o;\nuse(c);\n",
            id="nested-pattern",
        ),
        pytest.param(
            "const { a, b } = o;\nfoo();\n",
            [("a", 1, 9), ("b", 1, 12)],
            "foo();\n",
            id="emptied-pattern-with-pure-initializer",
        ),
        pytest.param(
            "const { a, b } = o;\nfoo();\n",
            [(ALL_DESTRUCTURED, 1, 7)],
            "foo();\n",
            id="all-destructured",
        ),
        pytest.param(
            "const a = [1], b = { k: `s`, m() {} }, c = -1 as number;\nfoo();\n",
            [(ALL_VARIABLES, 1, 1)],
            "foo();\n",
            id="all-variables-pure",
        ),
        pytest.param(
            "/** Helper. */\nfunction helper() {}\n\nexport const y = 1;\n",
            [("helper", 2, 10)],
            "export const y = 1;\n",
            id="function-with-jsdoc",
        ),
        pytest.param(
            "// section\nfunction helper() {}\nexport const y = 1;\n",
            [("helper", 2, 10)],
            "// section\nexport const y = 1;\n",
            id="plain-comment-kept",
        ),
        pytest.param(
            "type A = string;\ninterface B { x: A }\nexport {};\n",
            [("B", 2, 11)],
            "type A = string;\nexport {};\n",
            id="interface",
        ),
        pytest.param(
            "type Node = { next: Node };\nconst f = () => f();\nexport {};\n",
            [("Node", 1, 6), ("f", 2, 7)],
            "export {};\n",
            id="self-references-go-too",
        ),
        pytest.param(
            "declare const z: number;\nexport {};\n",
            [("z", 1, 15)],
            "export {};\n",
            id="ambient",
        ),
        pytest.param(
            "const 名前 = 1; const b = 2;\nuse(名前);\n",
            [("b", 1, 21)],
            "const 名前 = 1;\nuse(名前);\n",
            id="utf16-column-after-non-ascii",
        ),
        pytest.param(
            "const a = 1;\nuse(1);\n",
            [("a", 1, 99)],
            "use(1);\n",
            id="bad-column-falls-back-to-line",
        ),
    ],
)
def test_remove_unused_vars(source, targets, expected):
    text, _fixed, skipped = _fix(source, *targets)
    assert text == expected
    assert skipped == []


# tsc reports a pattern's only element at the pattern's opening bracket.
@needs_treesitter
@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        pytest.param(
            "const { a } = o;\nuse(1);\n", ("a", 1, 7), "use(1);\n", id="lone-shorthand"
        ),
        pytest.param(
            "const {\n  a,\n} = o;\nuse(1);\n", ("a", 1, 7), "use(1);\n", id="multiline"
        ),
        pytest.param(
            "const { a: b } = o, c = 1;\nuse(c);\n",
            ("b", 1, 7),
            "const c = 1;\nuse(c);\n",
            id="lone-renamed-other-declarator-kept",
        ),
        pytest.param(
            "const { a } = o;\nconst { a: x } = p;\nuse(x);\n",
            ("a", 1, 7),
            "const { a: x } = p;\nuse(x);\n",
            id="same-name-as-key-elsewhere",
        ),
        pytest.param(
            "const [a] = arr;\nuse(1);\n", ("a", 1, 7), "use(1);\n", id="array"
        ),
    ],
)
def test_lone_pattern_element_reported_at_pattern(source, target, expected):
    text, fixed, skipped = _fix(source, target)
    assert (text, fixed, skipped) == (expected, [target[0]], [])


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "target", "reason"),
    [
        pytest.param(
            "const { SITE_NAME } = process.env;\n",
            ("SITE_NAME", 1, 7),
            "would_empty_pattern",
            id="getter-initializer",
        ),
        pytest.param(
            "const { a } = f();\n",
            ("a", 1, 7),
            "would_empty_pattern",
            id="call-initializer",
        ),
        pytest.param(
            "const h = ({ p }) => 1;\nh({});\n",
            ("p", 1, 12),
            "function_param",
            id="param",
        ),
        pytest.param("const { a } = o;\n", ("zzz", 1, 7), "not_found", id="other-name"),
    ],
)
def test_pattern_position_skips(source, target, reason):
    text, fixed, skipped = _fix(source, target)
    assert (text, fixed, skipped) == (source, [], [reason])


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "target", "reason"),
    [
        pytest.param(
            "const a = f(), b = 2;\nuse(b);\n", ("a", 1, 7), "side_effects", id="call"
        ),
        pytest.param("const v = obj.prop;\n", ("v", 1, 7), "side_effects", id="getter"),
        pytest.param(
            "const { a, e = g() } = o;\nuse(a);\n",
            ("e", 1, 12),
            "side_effects",
            id="default-call",
        ),
        pytest.param(
            "const { [k()]: v, w } = o;\nuse(w);\n",
            ("v", 1, 16),
            "side_effects",
            id="computed-key",
        ),
        pytest.param(
            "let x = 1;\nx = 2;\n", ("x", 1, 5), "written_elsewhere", id="written-later"
        ),
        pytest.param(
            "function f(): void;\nfunction f(x?: number) {}\n",
            ("f", 2, 10),
            "written_elsewhere",
            id="overloads",
        ),
        pytest.param(
            "const { a, ...r } = o;\nuse(r);\n",
            ("a", 1, 9),
            "rest_element",
            id="rest-sibling",
        ),
        pytest.param(
            "const [a, b] = f();\nuse(b);\n",
            ("a", 1, 8),
            "array_destructuring",
            id="array",
        ),
        pytest.param(
            "function g(x) { return 1 }\n", ("x", 1, 12), "function_param", id="param"
        ),
        pytest.param(
            "const h = ({ p }) => 1;\nh({});\n",
            ("p", 1, 14),
            "function_param",
            id="pattern-param",
        ),
        pytest.param(
            "function h<T>() {}\nh();\n",
            ("T", 1, 12),
            "type_parameter",
            id="type-param",
        ),
        pytest.param(
            "for (let i = 0, j = 0; i < 1; i++) {}\n",
            ("j", 1, 17),
            "loop_variable",
            id="for-header",
        ),
        pytest.param(
            "if (c) var v = 1;\n", ("v", 1, 12), "other", id="unbraced-if-body"
        ),
        pytest.param(
            "let a = 1\ntype T = string\n(foo)()\n",
            ("T", 2, 6),
            "asi_hazard",
            id="no-semicolons",
        ),
        pytest.param("const a = 1;\n", ("zzz", 1, 7), "not_found", id="stale"),
    ],
)
def test_skips_what_could_change_behaviour(source, target, reason):
    text, fixed, skipped = _fix(source, target)
    assert text == source
    assert fixed == []
    assert skipped == [reason]


@needs_treesitter
def test_fix_unused_vars_writes_and_reports(tmp_path):
    ts_file = tmp_path / "a.tsx"
    ts_file.write_text(
        "const a = 1, b = f();\nexport const C = () => <div>{b}</div>;\n"
    )

    result = fix_unused_vars(
        [
            {
                "file": str(ts_file),
                "name": "a",
                "line": 1,
                "col": 7,
                "issue_id": "u::a.tsx::a:1",
            },
            {"file": str(ts_file), "name": "b", "line": 1, "col": 14},
        ]
    )

    assert (
        ts_file.read_text()
        == "const b = f();\nexport const C = () => <div>{b}</div>;\n"
    )
    [entry] = result.entries
    assert entry["fixed_issue_ids"] == ["u::a.tsx::a:1"]
    assert result.skip_reasons == {"side_effects": 1}


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vars_mod, "get_parser", lambda _grammar: None)
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("const a = 1;\n")

    result = fix_unused_vars([{"file": str(ts_file), "name": "a", "line": 1}])

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert ts_file.read_text() == "const a = 1;\n"
    assert "needs tree-sitter" in capsys.readouterr().err


@needs_treesitter
def test_asi_check_looks_past_comments_and_other_removals():
    source = "let a = 1\nconst b = 2;\n// note\nconst c = 3;\n(foo)()\n"

    text, fixed, skipped = _fix(source, ("b", 2, 7), ("c", 4, 7))

    # Removing both would leave `let a = 1\n(foo)()`, which reads as `1(foo)()`.
    assert fixed == ["b"]
    assert skipped == ["asi_hazard"]
    assert text == "let a = 1\n// note\nconst c = 3;\n(foo)()\n"


@needs_treesitter
def test_comment_after_removed_statement_is_not_an_asi_hazard():
    source = "let a = 1\nconst b = 2\n// note\nfoo()\n"

    text, fixed, _skipped = _fix(source, ("b", 2, 7))

    assert fixed == ["b"]
    assert text == "let a = 1\n// note\nfoo()\n"
