"""The dead-useeffect fixer removes effects on the syntax tree (roadmap 2.3, FX-17)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.useeffect as useeffect_mod
from desloppify.languages.typescript.fixers.useeffect import (
    fix_dead_useeffect,
    remove_dead_effects,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the dead-useeffect fixer needs tree-sitter",
)


def _fix(source: str, *lines: int, path: str = "a.tsx"):
    parsed = parse_text(source, path)
    out, fixed, skipped = remove_dead_effects(
        parsed, [{"line": line} for line in lines]
    )
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["line"] for entry in fixed], skipped


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "line", "expected"),
    [
        pytest.param(
            "foo();\nuseEffect(() => {\n}, []);\nbar();\n",
            2,
            "foo();\nbar();\n",
            id="multiline",
        ),
        pytest.param(
            "useEffect(() => {}, []);\nfoo();\n", 1, "foo();\n", id="one-line"
        ),
        pytest.param("useEffect(() => {});\nfoo();\n", 1, "foo();\n", id="no-deps"),
        pytest.param(
            "React.useEffect(() => {}, [a, b.c]);\nfoo();\n",
            1,
            "foo();\n",
            id="react-member",
        ),
        pytest.param(
            "useEffect(function () {\n  return;\n}, [id]);\nfoo();\n",
            1,
            "foo();\n",
            id="function-returning-nothing",
        ),
        pytest.param(
            "useEffect(() => {}, []); const later = 1;\n",
            1,
            "const later = 1;\n",
            id="code-on-same-line",
        ),
        pytest.param(
            "function C() {\n  // Load data on mount\n  useEffect(() => {\n  }, []);\n  return 1;\n}\n",
            3,
            "function C() {\n  // Load data on mount\n  return 1;\n}\n",
            id="keeps-comment-above",
        ),
        pytest.param(
            "useEffect(() => {}, [])\nfoo()\n", 1, "foo()\n", id="no-semicolons-safe"
        ),
    ],
)
def test_remove_dead_effects(source, line, expected):
    text, fixed, skipped = _fix(source, line)
    assert text == expected
    assert fixed == [line]
    assert skipped == []


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "line", "reason"),
    [
        pytest.param(
            "useEffect(() => {\n  load();\n}, []);\n", 1, "not_empty", id="statements"
        ),
        pytest.param(
            "useEffect(() => {\n  // TODO\n}, []);\n", 1, "not_empty", id="comment-only"
        ),
        pytest.param(
            "useEffect(() => {}, /* deps */ []);\n",
            1,
            "not_empty",
            id="comment-in-args",
        ),
        pytest.param(
            "useEffect(() => { return cleanup; });\n",
            1,
            "not_empty",
            id="returns-value",
        ),
        pytest.param(
            "useEffect(() => undefined, []);\n", 1, "not_empty", id="expression-body"
        ),
        pytest.param("useEffect(effect, []);\n", 1, "not_empty", id="named-callback"),
        pytest.param(
            "useEffect(() => {}, [load()]);\n", 1, "side_effects", id="call-in-deps"
        ),
        pytest.param(
            "useEffect(() => {}, [...deps]);\n", 1, "side_effects", id="spread-deps"
        ),
        pytest.param(
            "useEffect(() => {}, (n = 1, []));\n", 1, "side_effects", id="sequence-deps"
        ),
        pytest.param(
            "const x = useEffect(() => {}, []);\n", 1, "not_standalone", id="assigned"
        ),
        pytest.param(
            "if (a) useEffect(() => {}, []);\n",
            1,
            "not_standalone",
            id="unbraced-if-body",
        ),
        pytest.param(
            "const f = () => useEffect(() => {}, []);\n",
            1,
            "not_standalone",
            id="arrow-body",
        ),
        pytest.param(
            "let a = 1\nuseEffect(() => {}, []);\n(foo)()\n",
            2,
            "asi_hazard",
            id="no-semicolons",
        ),
        pytest.param(
            "const s = `\nuseEffect(() => {\n}, []);\n`;\n",
            2,
            "not_found",
            id="template-string",
        ),
        pytest.param("foo();\n", 1, "not_found", id="stale"),
    ],
)
def test_skips(source, line, reason):
    text, fixed, skipped = _fix(source, line)
    assert text == source
    assert fixed == []
    assert skipped == [reason]


@needs_treesitter
def test_fix_dead_useeffect_writes_and_reports(tmp_path):
    tsx_file = tmp_path / "a.tsx"
    tsx_file.write_text(
        "useEffect(() => {}, []);\nuseEffect(() => {}, [load()]);\nfoo();\n"
    )

    result = fix_dead_useeffect(
        [
            {
                "file": str(tsx_file),
                "line": 1,
                "smell_id": "dead_useeffect",
                "issue_id": "s::a",
            },
            {
                "file": str(tsx_file),
                "line": 2,
                "smell_id": "dead_useeffect",
                "issue_id": "s::a",
            },
        ]
    )

    assert tsx_file.read_text() == "useEffect(() => {}, [load()]);\nfoo();\n"
    [entry] = result.entries
    assert entry["removed"] == ["dead_useeffect"]
    assert result.skip_reasons == {"side_effects": 1}


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(useeffect_mod, "get_parser", lambda _grammar: None)
    tsx_file = tmp_path / "a.tsx"
    tsx_file.write_text("useEffect(() => {}, []);\n")

    result = fix_dead_useeffect([{"file": str(tsx_file), "line": 1}])

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert tsx_file.read_text() == "useEffect(() => {}, []);\n"
    assert "needs tree-sitter" in capsys.readouterr().err
