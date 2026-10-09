"""The debug-logs fixer removes whole statements only (roadmap 2.3, FX-2/FX-3)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.logs as logs_mod
from desloppify.languages.typescript.fixers.logs import (
    fix_debug_logs,
    remove_debug_logs,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the debug-logs fixer needs tree-sitter",
)


def _fix(source: str, *lines: int, path: str = "a.ts"):
    parsed = parse_text(source, path)
    out, fixed, skipped = remove_debug_logs(parsed, [{"line": line, "tag": "T"} for line in lines])
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["line"] for entry in fixed], skipped


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "lines", "expected"),
    [
        pytest.param(
            "function f() {\n  const x = 1;\n  console.log('[T] x', x, obj.a?.b, `v=${x}`);\n"
            "  return x;\n}\n",
            [3],
            "function f() {\n  const x = 1;\n  return x;\n}\n",
            id="statement-in-block",
        ),
        pytest.param(
            "console.log(\n  '[T] multi',\n  JSON.stringify(state),\n);\nfoo();\n",
            [1],
            "foo();\n",
            id="multiline-call",
        ),
        pytest.param(
            "p.catch(() => {});\nconsole.log('[T] a'); // why\nfoo();\n",
            [2],
            "p.catch(() => {});\nfoo();\n",
            id="neighbours-and-empty-callbacks-untouched",
        ),
        pytest.param(
            "console.log(`${DEBUG_TAG} hi`, x.toFixed(2), !ok, a ? b : c);\nfoo();\n",
            [1],
            "foo();\n",
            id="template-tag-and-pure-args",
        ),
        pytest.param(
            "switch (k) {\n  case 1:\n    console.log('[T] one');\n    break;\n}\n",
            [3],
            "switch (k) {\n  case 1:\n    break;\n}\n",
            id="switch-case",
        ),
        pytest.param(
            "if (debug) {\n  console.log('[T] a');\n}\n",
            [2],
            "if (debug) {\n}\n",
            id="empty-block-is-left",
        ),
        pytest.param(
            "function f() {\n  // section: catalog\n  // DEBUG: temporary logging\n"
            "  console.log('[T] test');\n  return 1;\n}\n",
            [4],
            "function f() {\n  // section: catalog\n  return 1;\n}\n",
            id="debug-comment-above-goes-too",
        ),
    ],
)
def test_remove_debug_logs(source, lines, expected):
    text, fixed, skipped = _fix(source, *lines)
    assert text == expected
    assert fixed == lines
    assert skipped == []


@needs_treesitter
def test_jsx_handler_body():
    text, fixed, _skipped = _fix(
        "const C = () => <div onClick={() => { console.log('🔍 [T] click'); go(); }} />;\n",
        1,
        path="c.tsx",
    )
    assert fixed == [1]
    assert text == "const C = () => <div onClick={() => { go(); }} />;\n"


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "line", "reason"),
    [
        pytest.param(
            "if (debug) console.log('[T] a');\nelse go();\n", 1, "not_standalone", id="unbraced-if"
        ),
        pytest.param("const f = () => console.log('[T] a');\n", 1, "not_standalone", id="arrow-body"),
        pytest.param("x && console.log('[T] a');\n", 1, "not_standalone", id="in-expression"),
        pytest.param(
            "let a = 1\nconsole.log('[T] a')\n(foo)()\n", 2, "not_standalone", id="called-result"
        ),
        pytest.param(
            "async function f() {\n  console.log('[T] saved', await save());\n}\n",
            2,
            "side_effects",
            id="await",
        ),
        pytest.param("console.log('[T] n', i++);\n", 1, "side_effects", id="update"),
        pytest.param("console.log('[T]', load());\n", 1, "side_effects", id="call"),
        pytest.param("console.log('[T]', ...args);\n", 1, "side_effects", id="spread"),
        pytest.param(
            "const log = (m) => { console.log('[T]', m); };\n", 1, "logger_wrapper", id="wrapper"
        ),
        pytest.param(
            "let a = 1\nconsole.log('[T] a')\n;(foo)()\n", 2, "asi_hazard", id="no-semicolons"
        ),
        pytest.param("console.log('not tagged');\n", 1, "not_found", id="untagged"),
    ],
)
def test_skips(source, line, reason):
    text, fixed, skipped = _fix(source, line)
    assert text == source
    assert fixed == []
    assert skipped == [reason]


@needs_treesitter
def test_fix_debug_logs_writes_and_reports(tmp_path):
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("console.log('[T] a');\nconsole.log('[T] b', load());\nfoo();\n")

    result = fix_debug_logs(
        [
            {"file": str(ts_file), "line": 1, "tag": "T", "issue_id": "logs::a.ts::T"},
            {"file": str(ts_file), "line": 2, "tag": "T", "issue_id": "logs::a.ts::T"},
        ]
    )

    assert ts_file.read_text() == "console.log('[T] b', load());\nfoo();\n"
    [entry] = result.entries
    assert entry["tags"] == ["T"]
    assert entry["log_count"] == 2
    # One of the two logs behind the grouped issue was kept, so the issue stays open.
    assert entry["fixed_issue_ids"] == ["logs::a.ts::T"]
    assert result.skip_reasons == {"side_effects": 1}


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(logs_mod, "get_parser", lambda _grammar: None)
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("console.log('[T] a');\n")

    result = fix_debug_logs([{"file": str(ts_file), "line": 1, "tag": "T"}])

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert ts_file.read_text() == "console.log('[T] a');\n"
    assert "needs tree-sitter" in capsys.readouterr().err
