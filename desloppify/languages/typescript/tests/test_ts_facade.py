"""Tests for TypeScript re-export facade detection."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.detectors.facade as facade_mod
from desloppify.languages.typescript.detectors.facade import (
    detect_reexport_facades,
    is_ts_facade,
)

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the syntax-tree facade check needs tree-sitter",
)

# Facades recognised both on the syntax tree and by the regex fallback.
_FACADES = [
    pytest.param("export { a, b } from './x';\n", ["./x"], id="named"),
    pytest.param("export {\n  a,\n  b as c,\n} from './x';\n", ["./x"], id="multi-line"),
    pytest.param("export * from './x';\n", ["./x"], id="star"),
    pytest.param("export * as ns from './x';\n", ["./x"], id="star-as"),
    pytest.param("export type { T } from './t';\n", ["./t"], id="type-named"),
    pytest.param("export type * from './t';\n", ["./t"], id="type-star"),
    pytest.param("export type * as T from './t';\n", ["./t"], id="type-star-as"),
    pytest.param("export { default } from './d';\n", ["./d"], id="default"),
    pytest.param('"use strict"\nexport * from "./x"\n', ["./x"], id="use-strict-no-semicolons"),
    pytest.param("#!/usr/bin/env node\nexport * from './x';\n", ["./x"], id="hashbang"),
    pytest.param(
        "/**\n * Public API.\n */\n// see http://example.com\nexport * from './a'; // a\nexport * from './b';\n",
        ["./a", "./b"],
        id="comments",
    ),
]

# Files that are not facades on either path.
_NOT_FACADES = [
    pytest.param("", id="empty"),
    pytest.param("// only a comment\n/* and another */\n", id="comments-only"),
    pytest.param("'use client';\n", id="directive-only"),
    pytest.param("export * from './x';\nconst y = 1;\n", id="declaration"),
    pytest.param("export * from './x';\nexport const y = 1;\n", id="exported-declaration"),
    pytest.param("export * from './x';\nexport default function f() {}\n", id="default-function"),
    pytest.param("import './polyfill';\nexport * from './x';\n", id="side-effect-import"),
    pytest.param("export * from './x';\n'use client';\n", id="late-directive"),
    # A file opening with a Next.js boundary directive is load-bearing (roadmap 2.28).
    pytest.param("'use client';\n\nexport { Button } from './button';\n", id="use-client"),
    pytest.param('"use server"\nexport * from "./actions"\n', id="use-server"),
    pytest.param(
        "// Client boundary.\n/* see docs */\n'use client';\nexport * from './x';\n",
        id="comments-before-use-client",
    ),
    pytest.param(
        "'use strict';\n// note\n\"use client\"\nexport { Button } from './button';\n",
        id="use-client-later-in-prologue",
    ),
    pytest.param("export * from './x';\nsetup();\n", id="call"),
    pytest.param("export { a };\n", id="local-export-without-import"),
]


def _facade(tmp_path, source: str, name: str = "index.ts") -> dict | None:
    f = tmp_path / name
    f.write_text(source)
    return is_ts_facade(str(f))


@needs_treesitter
@pytest.mark.parametrize(("source", "sources"), _FACADES)
@pytest.mark.parametrize("name", ["index.ts", "index.tsx", "index.js"])
def test_reexport_forms_are_facades(tmp_path, source, sources, name):
    result = _facade(tmp_path, source, name)
    assert result is not None
    assert result["imports_from"] == sources
    assert result["loc"] == len(source.splitlines())


@needs_treesitter
@pytest.mark.parametrize("source", _NOT_FACADES)
def test_non_facades(tmp_path, source):
    assert _facade(tmp_path, source) is None


@needs_treesitter
@pytest.mark.parametrize(
    ("source", "sources"),
    [
        pytest.param("import { a } from './x';\nexport { a };\n", ["./x"], id="named"),
        pytest.param("import { a as b } from './x';\nexport { b as c };\n", ["./x"], id="renamed"),
        pytest.param("import * as ns from './x';\nexport { ns };\n", ["./x"], id="namespace"),
        pytest.param("import D from './d';\nexport default D;\n", ["./d"], id="default"),
        pytest.param(
            "import { type T, a } from './x';\nexport * from './y';\nexport { type T, a };\n",
            ["./y", "./x"],
            id="mixed-with-reexport",
        ),
    ],
)
def test_import_then_export_is_facade(tmp_path, source, sources):
    result = _facade(tmp_path, source)
    assert result is not None
    assert result["imports_from"] == sources


@needs_treesitter
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import { a } from './x';\nexport { a, b };\n", id="unimported-name"),
        pytest.param("import { a } from './x';\nexport const b = a;\n", id="uses-import"),
        pytest.param("import { a } from './x';\n", id="import-only"),
        pytest.param("import x = require('./x');\nexport = x;\n", id="import-require"),
        pytest.param("export * from './x'\nexport type * from\n", id="syntax-error"),
        pytest.param("'use client';\nimport { a } from './x';\nexport { a };\n", id="use-client"),
    ],
)
def test_import_then_export_non_facades(tmp_path, source):
    assert _facade(tmp_path, source) is None


class TestWithoutTreeSitter:
    """The regex fallback handles the ``export ... from`` forms only."""

    @pytest.fixture(autouse=True)
    def _no_parser(self, monkeypatch):
        monkeypatch.setattr(facade_mod, "parse_text", lambda _text, _path: None)

    @pytest.mark.parametrize(("source", "sources"), _FACADES)
    def test_reexport_forms_are_facades(self, tmp_path, source, sources):
        result = _facade(tmp_path, source)
        assert result is not None
        assert result["imports_from"] == sources

    @pytest.mark.parametrize("source", _NOT_FACADES)
    def test_non_facades(self, tmp_path, source):
        assert _facade(tmp_path, source) is None

    def test_import_then_export_not_recognised(self, tmp_path):
        assert _facade(tmp_path, "import { a } from './x';\nexport { a };\n") is None


def _make_graph_entry(importer_count: int = 0) -> dict:
    return {
        "imports": set(),
        "importers": set(),
        "import_count": 0,
        "importer_count": importer_count,
    }


class TestIsTsFacade:
    def test_pure_reexport(self, tmp_path):
        f = tmp_path / "index.ts"
        f.write_text("export { foo, bar } from './module';\nexport * from './other';\n")
        result = is_ts_facade(str(f))
        assert result is not None
        assert "./module" in result["imports_from"]
        assert "./other" in result["imports_from"]

    def test_file_with_logic_not_facade(self, tmp_path):
        f = tmp_path / "real.ts"
        f.write_text("export { foo } from './module';\nconst x = 1;\n")
        result = is_ts_facade(str(f))
        assert result is None

    def test_type_reexport(self, tmp_path):
        f = tmp_path / "types.ts"
        f.write_text("export type { MyType } from './types';\n")
        result = is_ts_facade(str(f))
        assert result is not None

    def test_comments_allowed(self, tmp_path):
        f = tmp_path / "index.ts"
        f.write_text("// Re-exports\nexport { foo } from './module';\n")
        result = is_ts_facade(str(f))
        assert result is not None

    def test_empty_file_not_facade(self, tmp_path):
        f = tmp_path / "empty.ts"
        f.write_text("")
        result = is_ts_facade(str(f))
        assert result is None

    def test_nonexistent_file(self):
        result = is_ts_facade("/nonexistent/path/index.ts")
        assert result is None


class TestDetectReexportFacades:
    def test_facade_file_detected(self, tmp_path):
        f = tmp_path / "index.ts"
        f.write_text("export { foo } from './module';\nexport * from './other';\n")

        graph = {str(f): _make_graph_entry(importer_count=0)}
        entries, total = detect_reexport_facades(graph)
        assert len(entries) == 1
        assert entries[0]["kind"] == "file"
        assert total == 1

    def test_non_facade_file_not_detected(self, tmp_path):
        f = tmp_path / "real.ts"
        f.write_text("export { foo } from './module';\nconst x = 1;\n")

        graph = {str(f): _make_graph_entry(importer_count=1)}
        entries, total = detect_reexport_facades(graph)
        assert entries == []
        assert total == 1

    def test_too_many_importers_excluded(self, tmp_path):
        f = tmp_path / "index.ts"
        f.write_text("export * from './core';\n")

        graph = {str(f): _make_graph_entry(importer_count=21)}
        entries, total = detect_reexport_facades(graph)
        assert entries == []
        assert total == 1

    def test_default_threshold_allows_moderate_importers(self, tmp_path):
        f = tmp_path / "index.ts"
        f.write_text("export * from './core';\n")

        graph = {str(f): _make_graph_entry(importer_count=3)}
        entries, total = detect_reexport_facades(graph)
        assert len(entries) == 1
        assert total == 1

    def test_empty_graph(self):
        entries, total = detect_reexport_facades({})
        assert entries == []
        assert total == 0
