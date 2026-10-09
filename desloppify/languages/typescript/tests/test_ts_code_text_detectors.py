"""Line detectors ignore comments, strings and template text (roadmap 2.11).

Each case runs on the syntax tree and again with tree-sitter switched off,
where the detectors fall back to regexes over ``SourceText``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.engine.detectors.security.detector import detect_security_issues
from desloppify.languages._framework.node.js_text import literal_spans
from desloppify.languages.typescript.detectors.concerns import detect_mixed_concerns
from desloppify.languages.typescript.detectors.logs import detect_logs
from desloppify.languages.typescript.detectors.security.detector import (
    detect_ts_security,
)
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.phases_config import TS_COMPLEXITY_SIGNALS
from desloppify.languages.typescript.syntax.scanner import SourceText, jsx_text_spans

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


def _smell_lines(
    tmp_path: Path, content: str, name: str = "a.ts"
) -> dict[str, list[int]]:
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
    assert [
        i for i, _ in SourceText("f('rgba(1'); rgba(2)").line_matches(r"rgba\(")
    ] == [0]


@needs_treesitter
def test_jsx_text_is_not_code(tmp_path):
    text = "const a = <p>Don't {x} //stop é</p>; f('q'); // c\nconst b = <b>it's</b>; g();\n"
    source = SourceText(text, tmp_path / "a.tsx")
    assert source.code_lines == [
        "const a = <p>" + " " * 6 + "{x}" + " " * 9 + "</p>; f(   );     ",
        "const b = <b>    </b>; g();",
    ]
    assert source.kind_at(text.index("Don")) == "jsx"
    # The same text in a .ts file has no JSX; without a path the lexer can't tell.
    assert jsx_text_spans(text, tmp_path / "a.ts") == []
    assert SourceText(text).code_lines[0].startswith("const a = <p>Don ")


@needs_treesitter
def test_jsx_text_spans_reuse_the_file_parse(tmp_path):
    path = tmp_path / "a.jsx"
    path.write_text("const a = <p>x</p>;\n")
    assert jsx_text_spans(path.read_text(), path) == [(13, 14)]
    # Text that differs from the file on disk is parsed on its own.
    assert jsx_text_spans("const b = <i>yz</i>;\n", path) == [(13, 15)]


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
        'const u = "https://example.com/x";\n'
        '// "https://example.com/in-comment"\n'
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
    (tmp_path / "a.ts").write_text(
        "x();\nconsole.log(\n  `[Sync] ${n} done`,\n  n,\n);\n"
    )
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
    assert [(e["detail"]["kind"], e["detail"]["line"]) for e in entries] == [
        ("eval_injection", 11)
    ]


def _cross_language_security(
    tmp_path: Path, content: str, name: str = "a.ts"
) -> list[tuple[str, int]]:
    (tmp_path / name).write_text(content)
    entries, _ = detect_security_issues([name], None, "typescript", scan_root=tmp_path)
    return [(e["detail"]["kind"], e["detail"]["line"]) for e in entries]


def test_cross_language_security_finds_secrets_in_any_text(tmp_path):
    content = (
        "const apiKey = 'sk_"
        "live_abcdefghijklmnopqrstuvwx';\n"  # 1: in a string (split so push protection passes)
        "// AKIAIOSFODNN7EXAMPLE\n"  # 2: in a comment
        "const password = `Qwerty123456`;\n"  # 3: a template is a literal too
    )
    assert _cross_language_security(tmp_path, content) == [
        ("hardcoded_secret_value", 1),
        ("hardcoded_secret_value", 2),
        ("hardcoded_secret_name", 3),
    ]


def test_cross_language_security_names_and_calls_are_code(tmp_path):
    content = (
        "/**\n"
        " * @example\n"
        "\tapi({ token: 'secret123abc' })\n"  # 3: in a doc comment
        " */\n"
        "const msg = \"token = 'Zx81sk29Fq0p'\";\n"  # 5: in a string
        "f(); // const secret = 'Zx81sk29Fq0p'\n"  # 6: after code, in a comment
        "console.error('Invalid token');\n"  # 7: a message mentions a token
        "console.log(`token is ${token}`);\n"  # 8: logs one
        "const x = Math.random(); // not a session id\n"  # 9
        "const sessionId = Math.random().toString(36);\n"  # 10
        "localStorage.setItem('nonce', Math.random());\n"  # 11: a string key is context
        "const s = 'Math.random() token';\n"  # 12
        "const o = { rejectUnauthorized: false };\n"  # 13
        "const d = 'rejectUnauthorized: false';\n"  # 14
    )
    assert _cross_language_security(tmp_path, content) == [
        ("log_sensitive", 8),
        ("insecure_random", 10),
        ("insecure_random", 11),
        ("weak_crypto_tls", 13),
    ]


@needs_treesitter
def test_cross_language_security_reads_jsx_text(tmp_path):
    content = (
        "export const A = () => <p>Don't share your token = 'Zx81sk29Fq0p'</p>;\n"
        "export const B = () => <p>It's</p>; const secret = 'Zx81sk29Fq0p';\n"
    )
    assert _cross_language_security(tmp_path, content, "a.tsx") == [
        ("hardcoded_secret_name", 2)
    ]


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
