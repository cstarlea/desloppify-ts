"""The empty-if-chain fixer follows chains on the syntax tree (roadmap 2.3, FX-10)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.if_chain as if_chain_mod
from desloppify.languages.typescript.fixers.if_chain import (
    fix_empty_if_chain,
    remove_empty_if_chains,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the empty-if-chain fixer needs tree-sitter",
)


def _fix(source: str, *lines: int, path: str = "a.ts"):
    parsed = parse_text(source, path)
    out, fixed, skipped = remove_empty_if_chains(parsed, [{"line": line} for line in lines])
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["line"] for entry in fixed], skipped


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "line", "expected"),
    [
        pytest.param(
            "foo();\nif (a) {\n} else if (b.c) {\n} else {\n}\nbar();\n",
            2,
            "foo();\nbar();\n",
            id="multiline-chain",
        ),
        pytest.param("if (x) {} else {}\nfoo();\n", 1, "foo();\n", id="one-line-chain"),
        pytest.param("if (a) ; else {}\nfoo();\n", 1, "foo();\n", id="empty-statement-branch"),
        pytest.param(
            "function f() {\n  if (!ready || n > 2) {\n  }\n  return 1;\n}\n",
            2,
            "function f() {\n  return 1;\n}\n",
            id="in-function",
        ),
    ],
)
def test_remove_empty_if_chains(source, line, expected):
    text, fixed, skipped = _fix(source, line)
    assert text == expected
    assert fixed == [line]
    assert skipped == []


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "line", "reason"),
    [
        pytest.param(
            "if (a) {\n} else {\n  doThing();\n}\n", 1, "not_empty", id="non-empty-else"
        ),
        pytest.param("if (a) {} else if (b) { x(); }\n", 1, "not_empty", id="non-empty-else-if"),
        pytest.param("if (a) {\n  // todo\n}\n", 1, "not_empty", id="comment-only-block"),
        pytest.param("if (save()) {}\n", 1, "side_effects", id="call-in-condition"),
        pytest.param("if (x = y) {}\n", 1, "side_effects", id="assignment-in-condition"),
        pytest.param(
            "if (a) {} else if (next()) {}\n", 1, "side_effects", id="call-in-later-condition"
        ),
        pytest.param("for (;;) if (a) {}\n", 1, "not_standalone", id="unbraced-loop-body"),
        pytest.param("let a = 1\nif (a) {}\n(foo)()\n", 2, "asi_hazard", id="no-semicolons"),
        pytest.param("foo();\n", 1, "not_found", id="stale"),
    ],
)
def test_skips(source, line, reason):
    text, fixed, skipped = _fix(source, line)
    assert text == source
    assert fixed == []
    assert skipped == [reason]


@needs_treesitter
def test_fix_empty_if_chain_writes_and_reports(tmp_path):
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("if (a) {}\nif (b()) {}\nfoo();\n")

    result = fix_empty_if_chain(
        [
            {"file": str(ts_file), "line": 1, "smell_id": "empty_if_chain", "issue_id": "s::a"},
            {"file": str(ts_file), "line": 2, "smell_id": "empty_if_chain", "issue_id": "s::a"},
        ]
    )

    assert ts_file.read_text() == "if (b()) {}\nfoo();\n"
    [entry] = result.entries
    assert entry["removed"] == ["empty_if_chain"]
    assert result.skip_reasons == {"side_effects": 1}


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(if_chain_mod, "get_parser", lambda _grammar: None)
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("if (a) {}\n")

    result = fix_empty_if_chain([{"file": str(ts_file), "line": 1}])

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert ts_file.read_text() == "if (a) {}\n"
    assert "needs tree-sitter" in capsys.readouterr().err
