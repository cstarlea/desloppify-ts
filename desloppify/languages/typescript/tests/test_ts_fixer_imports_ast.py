"""The unused-imports fixer edits syntax-tree ranges (roadmap 2.3, FX-5/FX-6)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.fixers.imports as imports_mod
from desloppify.languages.typescript.fixers.imports import (
    ENTIRE_IMPORT,
    fix_unused_imports,
    remove_unused_imports,
)
from desloppify.languages.typescript.syntax.tree import parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the unused-imports fixer needs tree-sitter",
)


def _fix(source: str, *names_and_lines: tuple[str, int], path: str = "a.ts"):
    parsed = parse_text(source, path)
    entries = [{"name": name, "line": line} for name, line in names_and_lines]
    out, fixed = remove_unused_imports(parsed, entries)
    text = out.decode("utf-8")
    assert count_syntax_errors(text, path) == 0, text
    return text, [entry["name"] for entry in fixed]


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "targets", "expected"),
    [
        pytest.param(
            "import 'reflect-metadata'\nimport { a, b } from 'lib'\nuse(b)\n",
            [("a", 2)],
            "import 'reflect-metadata'\nimport { b } from 'lib'\nuse(b)\n",
            id="no-semicolons-after-side-effect-import",
        ),
        pytest.param(
            "import { a, /* } */ b, c } from 'lib';\nuse(b);\n",
            [("a", 1), ("c", 1)],
            "import { b } from 'lib';\nuse(b);\n",
            id="brace-in-comment",
        ),
        pytest.param(
            "import { x as y, y as z } from 'lib';\nuse(z);\n",
            [("y", 1)],
            "import { y as z } from 'lib';\nuse(z);\n",
            id="alias-matched-by-local-name",
        ),
        pytest.param(
            "import d, { a } from 'x';\nuse(a);\n",
            [("d", 1)],
            "import { a } from 'x';\nuse(a);\n",
            id="default-removed-named-kept",
        ),
        pytest.param(
            "import d, { a, b } from 'x';\nuse(d);\n",
            [("a", 1), ("b", 1)],
            "import d from 'x';\nuse(d);\n",
            id="all-named-removed-default-kept",
        ),
        pytest.param(
            "import {\n  a,\n  b,\n  c,\n} from 'x';\nuse(a, c);\n",
            [("b", 3)],
            "import {\n  a,\n  c,\n} from 'x';\nuse(a, c);\n",
            id="multiline-middle",
        ),
        pytest.param(
            "import {\n  a,\n  b,\n  c,\n} from 'x';\nuse(a, b);\n",
            [("c", 4)],
            "import {\n  a,\n  b,\n} from 'x';\nuse(a, b);\n",
            id="multiline-last-keeps-trailing-comma",
        ),
        pytest.param(
            "import e, * as ns from 'z' with { type: 'json' };\nuse(e);\n",
            [("ns", 1)],
            "import e from 'z' with { type: 'json' };\nuse(e);\n",
            id="namespace-with-attributes",
        ),
        pytest.param(
            "import { type A, B } from 'x';\nconst b: B = 1;\n",
            [("A", 1)],
            "import { B } from 'x';\nconst b: B = 1;\n",
            id="type-modifier",
        ),
        pytest.param(
            "// header\n\nimport { a } from 'a';\n\nimport { b } from 'b';\nuse(b);\n",
            [(ENTIRE_IMPORT, 3)],
            "// header\n\nimport { b } from 'b';\nuse(b);\n",
            id="entire-import-collapses-blank-lines",
        ),
        pytest.param(
            "import { a } from 'a'; // why\nfoo();\n",
            [("a", 1)],
            "foo();\n",
            id="trailing-comment-goes-with-statement",
        ),
        pytest.param(
            "import { a } from 'a'; foo();\n",
            [("a", 1)],
            "foo();\n",
            id="shared-line",
        ),
        pytest.param(
            "import f = require('f');\nfoo();\n",
            [("f", 1)],
            "foo();\n",
            id="import-equals-require",
        ),
        pytest.param(
            "import 'polyfill';\nfoo();\n",
            [(ENTIRE_IMPORT, 1)],
            "import 'polyfill';\nfoo();\n",
            id="side-effect-import-never-removed",
        ),
        pytest.param(
            "import { a } from 'x';\nuse(a);\n",
            [("missing", 1)],
            "import { a } from 'x';\nuse(a);\n",
            id="unknown-name-is-a-no-op",
        ),
    ],
)
def test_remove_unused_imports(source, targets, expected):
    text, _removed = _fix(source, *targets)
    assert text == expected


@needs_treesitter
def test_fixed_entries_follow_entry_order_and_skip_misses():
    _text, removed = _fix(
        "import { a, b } from 'x';\nimport { c } from 'y';\nuse(b);\n",
        ("c", 2),
        ("nope", 1),
        ("a", 1),
    )
    assert removed == ["c", "a"]


@needs_treesitter
def test_jsx_file_uses_tsx_grammar():
    text, removed = _fix(
        "import { a, b } from 'x';\nexport const C = () => <div>{b}</div>;\n",
        ("a", 1),
        path="c.tsx",
    )
    assert removed == ["a"]
    assert text.startswith("import { b } from 'x';")


def test_without_treesitter_nothing_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(imports_mod, "get_parser", lambda _grammar: None)
    ts_file = tmp_path / "a.ts"
    ts_file.write_text("import { a } from 'x';\n")

    result = fix_unused_imports(
        [{"file": str(ts_file), "name": "a", "line": 1, "category": "imports"}]
    )

    assert result.entries == []
    assert result.skip_reasons == {"needs_treesitter": 1}
    assert ts_file.read_text() == "import { a } from 'x';\n"
    assert "needs tree-sitter" in capsys.readouterr().err
