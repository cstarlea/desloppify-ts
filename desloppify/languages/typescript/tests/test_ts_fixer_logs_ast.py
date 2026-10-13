"""The debug-logs fixer removes whole statements only (roadmap 2.3, FX-2/FX-3)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.logs as logs_mod
from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.languages.typescript.detectors.logs import detect_logs
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
    out, fixed, skipped = remove_debug_logs(
        parsed, [{"line": line, "tag": "T"} for line in lines]
    )
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
            "if (ready) {\n  console.log('[T] a');\n  go();\n}\n",
            [2],
            "if (ready) {\n  go();\n}\n",
            id="guard-block-keeps-a-statement",
        ),
        pytest.param(
            "for (const x of xs) {\n  console.log('[T] x', x);\n}\n",
            [2],
            "for (const x of xs) {\n}\n",
            id="empty-loop-block-is-left",
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
            "if (ready) console.log('[T] a');\nelse go();\n",
            1,
            "not_standalone",
            id="unbraced-if",
        ),
        pytest.param(
            "const f = () => console.log('[T] a');\n",
            1,
            "not_standalone",
            id="arrow-body",
        ),
        pytest.param(
            "x && console.log('[T] a');\n", 1, "not_standalone", id="in-expression"
        ),
        pytest.param(
            "let a = 1\nconsole.log('[T] a')\n(foo)()\n",
            2,
            "not_standalone",
            id="called-result",
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
            "const log = (m) => { console.log('[T]', m); };\n",
            1,
            "logger_wrapper",
            id="wrapper",
        ),
        pytest.param(
            "let a = 1\nconsole.log('[T] a')\n;(foo)()\n",
            2,
            "asi_hazard",
            id="no-semicolons",
        ),
        pytest.param("console.log('not tagged');\n", 1, "not_found", id="untagged"),
        pytest.param(
            "console.warn('[T] config missing');\n",
            1,
            "not_debug_level",
            id="warn",
        ),
        pytest.param(
            "console.error('[T] failed', err);\n",
            1,
            "not_debug_level",
            id="error",
        ),
        pytest.param(
            "if (process.env.NODE_ENV !== 'production') {\n"
            "  console.log('[T] dev only');\n  go();\n}\n",
            2,
            "env_guard",
            id="node-env-guard",
        ),
        pytest.param(
            "if (isDev) {\n  console.log('[T] dev only');\n  go();\n}\n",
            2,
            "env_guard",
            id="is-dev-guard",
        ),
        pytest.param(
            "if (import.meta.env.DEV) {\n  console.log('[T] dev only');\n  go();\n}\n",
            2,
            "env_guard",
            id="import-meta-env-guard",
        ),
        pytest.param(
            "function f() {\n  if (isProduction) return;\n  console.log('[T] dev only');\n}\n",
            3,
            "env_guard",
            id="after-production-exit",
        ),
        pytest.param(
            "if (ready) {\n  console.log('[T] a');\n}\n",
            2,
            "empties_guard",
            id="empties-if-block",
        ),
        pytest.param(
            "if (ready) {\n  go();\n} else {\n  console.log('[T] a');\n}\n",
            4,
            "empties_guard",
            id="empties-else-block",
        ),
        pytest.param(
            "console.log('----');\nconsole.log('[T] a', x);\nconsole.log('----');\n",
            2,
            "partial_printout",
            id="part-of-a-printout",
        ),
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


# Round trips: the real detector feeds the fixer, which writes the file, and a
# second detect-and-fix pass changes nothing.
_DEV_LOGIN = r"""export async function mailLoginUrl(address: string, link: string) {
  if (!mailerReady()) {
    if (isProduction) {
      throw new Error('Mail delivery is not configured.')
    }

    // Local development: show the login URL in the terminal instead.
    console.log('\n\x1b[35m========================\x1b[0m')
    console.log('\x1b[35m  Login URL for \x1b[1m%s\x1b[0m', address)
    console.log('\x1b[35m  -> %s\x1b[0m', link)
    console.log('\x1b[35m========================\x1b[0m\n')
    return
  }
  await deliver(address, link)
}
"""


def _round_trip(tmp_path, source: str) -> tuple[str, list]:
    path = tmp_path / "src" / "a.ts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    first = fix_debug_logs(detect_logs(tmp_path).entries)
    clear_source_file_cache_for_tests()
    after = path.read_text()
    assert count_syntax_errors(after, "a.ts") == 0, after
    second = fix_debug_logs(detect_logs(tmp_path).entries)
    assert second.entries == []
    assert path.read_text() == after
    return after, first.entries


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "export function f(items: number[]) {\n  console.log(\n    '[Cart] items',\n"
            "    items.length,\n    JSON.stringify(items),\n  );\n  return items;\n}\n",
            "export function f(items: number[]) {\n  return items;\n}\n",
            id="multi-line-console-log",
        ),
        pytest.param(
            "export function f(n: number) {\n"
            "  console.log(`\\x1b[36m[Sync] ${n} rows\\x1b[0m`);\n  return n;\n}\n",
            "export function f(n: number) {\n  return n;\n}\n",
            id="template-literal-with-ansi-codes",
        ),
        pytest.param(_DEV_LOGIN, None, id="guarded-dev-printout-untouched"),
        pytest.param(
            "export function f() {\n  if (isDev) {\n    console.log('[Dev] state', state);\n"
            "    go();\n  }\n}\n",
            None,
            id="is-dev-block-untouched",
        ),
        pytest.param(
            "export function f(message: string) {\n  // Avoid importing the logger here.\n"
            "  console.warn(`[env] ${message}`);\n}\n",
            None,
            id="console-warn-untouched",
        ),
    ],
)
def test_round_trip(set_project_root, tmp_path, source, expected):
    after, fixed = _round_trip(tmp_path, source)
    assert after == (source if expected is None else expected)
    assert bool(fixed) == (expected is not None)


@needs_treesitter
def test_reported_dev_login_printout_is_refused(tmp_path):
    """Stale entries from the old ANSI-as-tag detector don't get the printout deleted."""
    path = tmp_path / "mailer.ts"
    path.write_text(_DEV_LOGIN)
    entries = [{"file": str(path), "line": n, "tag": "unknown"} for n in (8, 9, 10, 11)]
    result = fix_debug_logs(entries)
    assert result.entries == []
    assert path.read_text() == _DEV_LOGIN
    assert result.skip_reasons == {"not_found": 4}


@needs_treesitter
def test_tagged_line_of_a_printout_is_kept():
    source = (
        "function banner(url: string) {\n  console.log('==========');\n"
        "  console.log('\\x1b[36m[Link] %s\\x1b[0m', url);\n  console.log('==========');\n}\n"
    )
    text, fixed, skipped = _fix(source, 3)
    assert text == source
    assert fixed == []
    assert skipped == ["partial_printout"]
