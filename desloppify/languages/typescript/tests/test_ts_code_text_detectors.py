"""Line detectors ignore comments, strings and template text (roadmap 2.11).

Each case runs on the syntax tree and again with tree-sitter switched off,
where the detectors fall back to regexes over ``SourceText``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.languages._framework.node.js_text import literal_spans
from desloppify.languages.typescript.detectors.concerns import detect_mixed_concerns
from desloppify.languages.typescript.detectors.logs import detect_logs
from desloppify.languages.typescript.detectors.security.detector import detect_ts_security
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.phases_config import TS_COMPLEXITY_SIGNALS
from desloppify.languages.typescript.syntax.scanner import SourceText

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
        assert tree_mod.parse_text("x;", "a.ts") is None
    return request.param


def _smell_lines(tmp_path: Path, content: str, name: str = "a.ts") -> dict[str, list[int]]:
    (tmp_path / name).write_text(content)
    entries, _ = detect_smells(tmp_path)
    return {e["id"]: sorted(m["line"] for m in e["matches"]) for e in entries}


# ── the lexer ────────────────────────────────────────────────


def test_literal_spans_kinds():
    text = "a = 'x' + `t${b}u` / 2; // c\n/* d */ r = /re/g;"
    kinds = [(text[s:e], kind) for s, e, kind in literal_spans(text)]
    assert kinds == [
        ("'x'", "string"),
        ("`t${", "template"),
        ("}u`", "template"),
        ("// c", "comment"),
        ("/* d */", "comment"),
        ("/re/g", "regex"),
    ]


def test_source_text_anchors():
    source = SourceText("x = 1; // TODO a\ny = 'TODO b';\nz = `\nTODO c`;\n")
    assert [i for i, _ in source.line_matches("TODO", "comment")] == [0]
    assert list(source.line_matches("TODO")) == []
    assert [i for i, _ in source.line_matches("'TODO", "literal")] == [1]
    # The first match on a line may be in a string; a later one in code still counts.
    assert [i for i, _ in SourceText("f('rgba(1'); rgba(2)").line_matches(r"rgba\(")] == [0]


# ── smells ───────────────────────────────────────────────────


_NOT_CODE = """\
/*
 * x.sort(); if (a) {} void y;
 * try { a() } catch (e) {}
 */
// window.__x = 1; console.error(e); fetch(a) - 99999
const s = 'catch (e) {} rgba(0, 0, 0) x > 10000';
const re = /\\d{4}-12345/;
const t = `
  switch (k) { case 1: case 2: }
  try { a() } catch (e) { console.error(e) }
  color: '#fff' window.__x = 2
`;
"""


def test_smells_ignore_comments_strings_and_templates(tmp_path, mode):
    assert _smell_lines(tmp_path, _NOT_CODE) == {}


def test_smells_still_see_code_inside_substitutions(tmp_path, mode):
    found = _smell_lines(tmp_path, "const s = `${ms / 10000}s ${x.sort()}`;\n")
    assert found["magic_number"] == [1]
    assert found["sort_no_comparator"] == [1]


def test_comment_and_literal_anchored_smells(tmp_path, mode):
    content = (
        "const a = 'see // TODO not a comment';\n"
        "f(); // later // TODO: real\n"
        "const u = \"https://example.com/x\";\n"
        "// \"https://example.com/in-comment\"\n"
    )
    found = _smell_lines(tmp_path, content)
    assert found["todo_fixme"] == [2]
    assert found["hardcoded_url"] == [3]


@needs_treesitter
def test_structural_smells_on_the_tree(tmp_path):
    content = (
        "try { a() } catch {}\n"  # 1: empty, no binding
        "try { a() } catch (e) { /* expected */ }\n"  # 2: a comment is content
        "try {\n  a()\n} catch (e) {\n  console.error(\n    'x', e,\n  );\n}\n"  # 5: swallowed
        "switch (f(x)) { case 1: break; case 2: break; }\n"  # 10: nested parens
        "if (a) {}\nelse { b(); }\n"  # 11: not an empty chain
        "if (c) {} else if (d) {} else {}\n"  # 13: empty chain
        "void unused;\n"  # 14
        "items\n  .sort();\n"  # 16: reported where `sort` is
    )
    found = _smell_lines(tmp_path, content)
    assert found["empty_catch"] == [1]
    assert found["swallowed_error"] == [5]
    assert found["switch_no_default"] == [10]
    assert found["empty_if_chain"] == [13]
    assert found["voided_symbol"] == [14]
    assert found["sort_no_comparator"] == [16]


# ── logs ─────────────────────────────────────────────────────


def test_logs_ignore_comments_and_strings(tmp_path, mode):
    (tmp_path / "a.ts").write_text(
        "// console.log('[Old] x');\n"
        "const s = \"console.log('[Str] x')\";\n"
        "console.log('[Real] x');\n"
    )
    entries = detect_logs(tmp_path).entries
    assert [(e["line"], e["tag"]) for e in entries] == [(3, "Real")]


@needs_treesitter
def test_logs_find_multi_line_calls_where_they_start(tmp_path):
    (tmp_path / "a.ts").write_text("x();\nconsole.log(\n  `[Sync] ${n} done`,\n  n,\n);\n")
    entries = detect_logs(tmp_path).entries
    assert [(e["line"], e["tag"]) for e in entries] == [(2, "Sync")]


# ── security ─────────────────────────────────────────────────


def test_security_ignores_comments_and_strings(tmp_path):
    path = tmp_path / "a.ts"
    path.write_text(
        "/**\n"
        " * @default JSON.parse()\n"
        " * eval(code) is never called\n"
        " */\n"
        "const html = `<div dangerouslySetInnerHTML={x} />`;\n"
        "const msg = 'el.innerHTML = value';\n"
        "try {\n"
        "  const s = '}';\n"
        "  JSON.parse(a);\n"
        "} catch {}\n"
        "eval(code);\n"
    )
    entries = detect_ts_security([str(path)], None).entries
    assert [(e["detail"]["kind"], e["detail"]["line"]) for e in entries] == [("eval_injection", 11)]


# ── file-level heuristics ────────────────────────────────────


def test_concerns_ignore_commented_code(tmp_path):
    body = "\n".join(f"const v{i} = {i};" for i in range(100))
    (tmp_path / "a.tsx").write_text(
        body + "\n// const q = useQuery(); xs.map(f).filter(g).reduce(h);\n"
        "export function A() { return (<div />); }\n"
    )
    entries, _ = detect_mixed_concerns(tmp_path)
    assert entries == []


def test_complexity_signals_count_code_and_comments_apart():
    signals = {signal.name: signal for signal in TS_COMPLEXITY_SIGNALS}
    content = "const a = '// TODO: no';\n// x // TODO: yes\nconst r = /a?b?c?d/;\n"
    assert signals["TODOs"].compute(content, []) == (1, "1 TODOs")
    ternaries = "const r = /(a?)b?(c?)d?e?/; const s = 'x ? y ? z';\n" * 3
    assert signals["nested ternaries"].compute(ternaries, []) is None
