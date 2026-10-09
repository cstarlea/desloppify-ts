"""The syntax gate that stops fixers and ``move`` from writing broken code."""

from __future__ import annotations

import textwrap

import pytest

import desloppify.languages.typescript.syntax.validation as validation_mod
from desloppify.app.commands.move.apply import check_rewrite_syntax
from desloppify.base.exception_sets import CommandError
from desloppify.languages.typescript.fixers.fixer_io import apply_fixer
from desloppify.languages.typescript.fixers.imports import fix_unused_imports
from desloppify.languages.typescript.fixers.vars import fix_unused_vars
from desloppify.languages.typescript.syntax.validation import (
    count_syntax_errors,
    grammar_for,
    syntax_regression,
)

pytest.importorskip("tree_sitter_language_pack")


# ── validation module ────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "grammar"),
    [
        ("a.ts", "typescript"),
        ("a.mts", "typescript"),
        ("a.cts", "typescript"),
        ("a.tsx", "tsx"),
        ("a.js", "tsx"),
        ("a.jsx", "tsx"),
        ("a.mjs", "tsx"),
    ],
)
def test_grammar_for(name, grammar):
    assert grammar_for(name) == grammar


def test_count_syntax_errors_valid_and_broken():
    assert count_syntax_errors("const a = 1;\nexport { a };\n", "a.ts") == 0
    assert count_syntax_errors("const { 2), b } = obj;\n", "a.ts") > 0


def test_angle_bracket_cast_is_valid_in_ts_files():
    assert count_syntax_errors("const a = <Foo>bar;\n", "a.ts") == 0


def test_jsx_parses_in_tsx_and_js_files():
    source = 'export const A = () => <div className="x">{1}</div>;\n'
    assert count_syntax_errors(source, "a.tsx") == 0
    assert count_syntax_errors(source, "a.js") == 0


def test_syntax_regression_only_flags_added_errors():
    broken = "function f( {\n"
    assert syntax_regression("a.ts", "const a = 1;\n", "const a = ;\n")
    assert syntax_regression("a.ts", "const a = 1;\n", "const b = 1;\n") is None
    # Already-broken files stay fixable as long as the edit adds no errors.
    assert syntax_regression("a.ts", broken + "const a = 1;\n", broken) is None


def test_syntax_regression_is_none_without_tree_sitter(monkeypatch):
    monkeypatch.setattr(validation_mod, "parse_text", lambda *_args: None)
    assert count_syntax_errors("const a = ;\n", "a.ts") is None
    assert syntax_regression("a.ts", "const a = 1;\n", "const a = ;\n") is None


# ── fixer write path ─────────────────────────────────────────


def test_apply_fixer_refuses_output_that_breaks_syntax(tmp_path, capsys):
    ts_file = tmp_path / "a.ts"
    original = "const a = f(1, 2);\nconsole.log(a);\n"
    ts_file.write_text(original)

    def transform(lines, _entries):
        return ["const a = f(1, \n", *lines[1:]], ["a"]

    for dry_run in (True, False):
        assert apply_fixer([{"file": str(ts_file)}], transform, dry_run=dry_run) == []
    assert ts_file.read_text() == original
    assert "syntax error" in capsys.readouterr().err


def test_apply_fixer_dry_run_returns_unified_diff(tmp_path):
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("const a = 1;\nconst b = 2;\nconsole.log(b);\n")

    def transform(lines, _entries):
        return [line for line in lines if "const a" not in line], ["a"]

    [result] = apply_fixer([{"file": str(ts_file)}], transform, dry_run=True)
    assert result["diff"].startswith("--- a/")
    assert "-const a = 1;" in result["diff"]
    added = [
        line
        for line in result["diff"].splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert added == []


def test_vars_destructuring_breakage_is_blocked(tmp_path):
    ts_file = tmp_path / "d.ts"
    original = "const { a = f(1, 2), b } = obj;\nconsole.log(b);\n"
    ts_file.write_text(original)
    entries = [{"file": str(ts_file), "name": "a", "line": 1, "category": "vars"}]

    result = fix_unused_vars(entries, dry_run=False)

    assert result.entries == []
    assert ts_file.read_text() == original


def test_imports_after_side_effect_import_is_rewritten_correctly(tmp_path):
    # The old line-based fixer wrote `import, { b } from 'lib';` here.
    ts_file = tmp_path / "i.ts"
    ts_file.write_text(
        textwrap.dedent("""\
            import 'reflect-metadata'
            import { a, b } from 'lib'
            console.log(b)
        """)
    )
    entries = [{"file": str(ts_file), "name": "a", "line": 2, "category": "imports"}]

    result = fix_unused_imports(entries, dry_run=False)

    assert [e["removed"] for e in result.entries] == [["a"]]
    assert ts_file.read_text() == (
        "import 'reflect-metadata'\nimport { b } from 'lib'\nconsole.log(b)\n"
    )


def test_valid_fix_still_applies(tmp_path):
    ts_file = tmp_path / "ok.ts"
    ts_file.write_text("import { a, b } from './lib';\nconsole.log(b);\n")
    entries = [{"file": str(ts_file), "name": "a", "line": 1, "category": "imports"}]

    result = fix_unused_imports(entries, dry_run=False)

    assert len(result.entries) == 1
    assert ts_file.read_text() == "import { b } from './lib';\nconsole.log(b);\n"


# ── move ─────────────────────────────────────────────────────


def test_check_rewrite_syntax_aborts_real_move(tmp_path):
    importer = tmp_path / "a.ts"
    importer.write_text("import { x } from './old';\n")
    changes = {str(importer): [("'./old'", "'./new")]}

    with pytest.raises(CommandError, match="would break syntax"):
        check_rewrite_syntax(changes, dry_run=False)
    assert importer.read_text() == "import { x } from './old';\n"


def test_check_rewrite_syntax_warns_on_dry_run(tmp_path, capsys):
    importer = tmp_path / "a.ts"
    importer.write_text("import { x } from './old';\n")

    check_rewrite_syntax({str(importer): [("'./old'", "'./new")]}, dry_run=True)

    assert "would abort" in capsys.readouterr().out


def test_check_rewrite_syntax_allows_valid_rewrites(tmp_path):
    importer = tmp_path / "a.ts"
    importer.write_text("import { x } from './old';\n")

    check_rewrite_syntax({str(importer): [("'./old'", "'./new'")]}, dry_run=False)
