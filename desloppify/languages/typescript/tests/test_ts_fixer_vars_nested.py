"""The unused-vars fixer removes nested destructuring patterns that end up empty (roadmap 2.26)."""

from __future__ import annotations

import importlib.util

import pytest

from desloppify.languages.typescript.fixers.vars import remove_unused_vars
from desloppify.languages.typescript.syntax.nodes import ALL_DESTRUCTURED
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the unused-vars fixer needs tree-sitter",
)


def _fix(source: str, *targets: tuple[str, int, int]):
    parsed = parse_text(source, "a.ts")
    entries = [{"name": name, "line": line, "col": col} for name, line, col in targets]
    out, fixed, skipped = remove_unused_vars(parsed, entries)
    text = out.decode("utf-8")
    assert count_syntax_errors(text, "a.ts") == 0, text
    return text, [entry["name"] for entry in fixed], skipped


# Positions are where tsc reports them: a pattern's only name at its bracket.
@needs_treesitter
@pytest.mark.parametrize(
    ("source", "targets", "expected"),
    [
        pytest.param(
            "const { a: { b } } = o;\nuse(1);\n",
            [("b", 1, 12)],
            "use(1);\n",
            id="whole-declaration",
        ),
        pytest.param(
            "const { x, a: { b } } = o;\nuse(x);\n",
            [("b", 1, 15)],
            "const { x } = o;\nuse(x);\n",
            id="pair-removed",
        ),
        pytest.param(
            "const { x, a: { b, c } } = o;\nuse(x);\n",
            [(ALL_DESTRUCTURED, 1, 15)],
            "const { x } = o;\nuse(x);\n",
            id="all-destructured-inner",
        ),
        pytest.param(
            "const { a: { b, c } } = o;\nuse(1);\n",
            [(ALL_DESTRUCTURED, 1, 12)],
            "use(1);\n",
            id="all-destructured-inner-empties-outer",
        ),
        pytest.param(
            "const { x, a: { b } = { b: 1 } } = o;\nuse(x);\n",
            [("b", 1, 15)],
            "const { x } = o;\nuse(x);\n",
            id="pure-default",
        ),
        pytest.param(
            "const { x, a: { b: { c } } } = o;\nuse(x);\n",
            [("c", 1, 20)],
            "const { x } = o;\nuse(x);\n",
            id="three-levels",
        ),
        pytest.param(
            "const { x, d: [p, q] } = o;\nuse(x);\n",
            [("p", 1, 16), ("q", 1, 19)],
            "const { x } = o;\nuse(x);\n",
            id="emptied-array",
        ),
        pytest.param(
            "const [{ b }] = arr;\nuse(1);\n",
            [("b", 1, 8)],
            "use(1);\n",
            id="array-element",
        ),
        pytest.param(
            "const { a: { b }, c } = o, d = 1;\nuse(d);\n",
            [("b", 1, 12), ("c", 1, 19)],
            "const d = 1;\nuse(d);\n",
            id="declarator-of-several",
        ),
        pytest.param(
            "const { a: { b }, c } = o;\nuse(c);\n",
            [("b", 1, 12)],
            "const { c } = o;\nuse(c);\n",
            id="first-member",
        ),
        pytest.param(
            "const {\n  x,\n  a: {\n    b,\n  },\n} = o;\nuse(x);\n",
            [("b", 3, 6)],
            "const {\n  x,\n} = o;\nuse(x);\n",
            id="multiline",
        ),
    ],
)
def test_removes_emptied_nested_patterns(source, targets, expected):
    text, fixed, skipped = _fix(source, *targets)
    assert (text, skipped) == (expected, [])
    assert len(fixed) == len(targets)


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "target", "reason"),
    [
        pytest.param(
            "const { a: { b } } = f();\n", ("b", 1, 12), "would_empty_pattern", id="call-initializer"
        ),
        pytest.param(
            "const { a: { b }, ...r } = o;\nuse(r);\n", ("b", 1, 12), "rest_element", id="rest-sibling"
        ),
        pytest.param(
            "const { a: { b, c }, ...r } = o;\nuse(r);\n",
            (ALL_DESTRUCTURED, 1, 12),
            "rest_element",
            id="all-destructured-rest-sibling",
        ),
        pytest.param(
            "const { x, a: { b } = g() } = o;\nuse(x);\n",
            ("b", 1, 15),
            "side_effects",
            id="default-call",
        ),
        pytest.param(
            "const { x, [k()]: { b } } = o;\nuse(x);\n",
            ("b", 1, 19),
            "side_effects",
            id="computed-key",
        ),
        pytest.param(
            "const [{ b }, c] = arr;\nuse(c);\n", ("b", 1, 8), "array_destructuring", id="array-sibling"
        ),
        pytest.param(
            "const { x, d: [p, q] } = o;\nuse(x, q);\n",
            ("p", 1, 16),
            "array_destructuring",
            id="array-partly-used",
        ),
        pytest.param(
            "let { a: { b } } = o;\nb = 1;\n", ("b", 1, 10), "written_elsewhere", id="written-later"
        ),
        pytest.param(
            "function g({ a: { b } }) {}\n", ("b", 1, 17), "function_param", id="param"
        ),
    ],
)
def test_skips_what_could_change_behaviour(source, target, reason):
    text, fixed, skipped = _fix(source, target)
    assert (text, fixed, skipped) == (source, [], [reason])
