"""Tests for TypeScript import extraction and the edges it puts in the graph."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import desloppify.base.discovery.paths as paths_api_mod
import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.imports as imports_mod
import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.base.discovery.source import find_ts_and_tsx_files
from desloppify.engine.detectors.graph import detect_cycles
from desloppify.languages.typescript.detectors.deps.imports import (
    DYNAMIC,
    DYNAMIC_PREFIX,
    GLOB,
    MOCK,
    REFERENCE,
    REQUIRE,
    SIDE_EFFECT,
    STATIC,
    ImportExtractor,
    ImportRef,
    extract_imports_regex,
)

_TREESITTER = ImportExtractor().uses_treesitter
needs_treesitter = pytest.mark.skipif(
    not _TREESITTER, reason="needs tree-sitter with the tsx grammar"
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root, monkeypatch):
    monkeypatch.setattr(paths_api_mod, "SRC_PATH", tmp_path / "src")
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()


def _write(root: Path, name: str, content: str = "export const x = 1;\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _key(root: Path, name: str) -> str:
    return str((root / name).resolve())


# ── extraction ───────────────────────────────────────────────


@needs_treesitter
def test_extractor_classifies_every_import_form(tmp_path):
    source = _write(
        tmp_path,
        "all.tsx",
        "\n".join(
            [
                '/// <reference path="./globals.d.ts" />',
                "import './side';",
                "import type { A } from './a';",
                "import { type B, type C } from './b';",
                "import D, { type E } from './d';",
                "import F = require('./f');",
                "import type G = require('./g');",
                "export type { I } from './i';",
                "export * as K from './k';",
                "export { type L } from './l';",
                "const m = await import('./m');",
                "const n = require('./n');",
                "vi.mock('./o');",
                "jest.mock(`./p`);",
                "// import { Q } from './q';",
                "const s = \"import { R } from './r'\";",
                "const t = (x: string) => import(`./t/${x}`);",
                "import.meta.glob(['./pages/*.tsx', '!./pages/_*.tsx']);",
            ]
        ),
    )
    refs = ImportExtractor().extract(str(source))
    assert refs == [
        ImportRef("./globals.d.ts", REFERENCE),
        ImportRef("./side", SIDE_EFFECT),
        ImportRef("./a", STATIC, type_only=True),
        ImportRef("./b", STATIC, type_only=True),
        ImportRef("./d", STATIC),
        ImportRef("./f", STATIC),
        ImportRef("./g", STATIC, type_only=True),
        ImportRef("./i", STATIC, type_only=True),
        ImportRef("./k", STATIC),
        ImportRef("./l", STATIC, type_only=True),
        ImportRef("./m", DYNAMIC),
        ImportRef("./n", REQUIRE),
        ImportRef("./o", MOCK),
        ImportRef("./p", MOCK),
        ImportRef("./t/", DYNAMIC_PREFIX),
        ImportRef("./pages/*.tsx", GLOB),
    ]


def test_regex_fallback_skips_comments_and_keeps_type_only():
    refs = extract_imports_regex(
        "import type { A } from './a';\n"
        "// import { B } from './b';\n"
        "/* import { C } from './c'; */\n"
        "import { D } from './d';\n"
        "const url = 'https://example.com'; import './e';\n"
    )
    by_spec = {ref.specifier: ref for ref in refs}
    assert set(by_spec) == {"./a", "./d"}
    assert by_spec["./a"].type_only
    assert not by_spec["./d"].type_only


# ── graph edges ──────────────────────────────────────────────


@needs_treesitter
def test_type_only_cycle_is_not_an_import_cycle(tmp_path):
    _write(tmp_path, "a.ts", "import type { B } from './b';\nexport const a = 1;\n")
    _write(tmp_path, "b.ts", "import { a } from './a';\nexport type B = typeof a;\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)

    assert _key(tmp_path, "b.ts") in graph[_key(tmp_path, "a.ts")]["imports"]
    assert _key(tmp_path, "b.ts") in graph[_key(tmp_path, "a.ts")]["deferred_imports"]
    cycles, _ = detect_cycles(graph)
    assert cycles == []


@needs_treesitter
def test_value_and_type_import_of_same_module_is_a_runtime_edge(tmp_path):
    _write(
        tmp_path,
        "a.ts",
        "import type { T } from './b';\nimport { b } from './b';\nexport const a = b;\n",
    )
    _write(tmp_path, "b.ts", "import { a } from './a';\nexport const b = 1;\nexport type T = typeof a;\n")
    cycles, _ = detect_cycles(deps_detector_mod.build_dep_graph(tmp_path))
    assert [c["length"] for c in cycles] == [2]


@pytest.mark.parametrize("treesitter", [True, False], ids=["treesitter", "regex"])
def test_imports_in_comments_create_no_edges(tmp_path, monkeypatch, treesitter):
    if treesitter and not _TREESITTER:
        pytest.skip("needs tree-sitter with the tsx grammar")
    if not treesitter:
        monkeypatch.setattr(imports_mod, "_parser", lambda: None)
    _write(tmp_path, "legacy.ts")
    _write(
        tmp_path,
        "main.ts",
        "// import { x } from './legacy';\n"
        "/* import { x } from './legacy'; */\n"
        "export const sample = 1;\n",
    )
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "legacy.ts")]["importers"] == set()


@needs_treesitter
def test_import_inside_a_string_creates_no_edge(tmp_path):
    _write(tmp_path, "legacy.ts")
    _write(tmp_path, "main.ts", "export const sample = \"import { x } from './legacy'\";\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "legacy.ts")]["importers"] == set()


@needs_treesitter
def test_dynamic_and_mock_imports_are_deferred_edges(tmp_path):
    _write(tmp_path, "lazy.ts")
    _write(tmp_path, "mocked.ts")
    _write(
        tmp_path,
        "main.test.ts",
        "vi.mock('./mocked');\nexport const load = () => import('./lazy');\n",
    )
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    main = graph[_key(tmp_path, "main.test.ts")]
    targets = {_key(tmp_path, "lazy.ts"), _key(tmp_path, "mocked.ts")}
    assert targets <= main["imports"]
    assert targets <= main["deferred_imports"]


@needs_treesitter
def test_import_meta_glob_links_every_match(tmp_path):
    _write(tmp_path, "src/widgets/Clock.tsx")
    _write(tmp_path, "src/widgets/nested/Deep.tsx")
    _write(tmp_path, "src/widgets/helper.ts")
    _write(tmp_path, "src/main.ts", "import.meta.glob('./widgets/**/*.tsx', { eager: true });\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    main = graph[_key(tmp_path, "src/main.ts")]
    assert main["imports"] == {
        _key(tmp_path, "src/widgets/Clock.tsx"),
        _key(tmp_path, "src/widgets/nested/Deep.tsx"),
    }


@needs_treesitter
def test_vite_root_relative_glob(tmp_path):
    _write(tmp_path, "src/pages/Home.tsx")
    _write(tmp_path, "src/router.ts", "import.meta.glob('/src/pages/*.tsx');\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "src/router.ts")]["imports"] == {
        _key(tmp_path, "src/pages/Home.tsx")
    }


@needs_treesitter
def test_template_literal_dynamic_import_links_files_under_prefix(tmp_path):
    _write(tmp_path, "src/locales/en.ts")
    _write(tmp_path, "src/locales/fr.ts")
    _write(tmp_path, "src/other.ts")
    _write(
        tmp_path,
        "src/i18n.ts",
        "export const load = (lang: string) => import(`./locales/${lang}.ts`);\n",
    )
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "src/i18n.ts")]["imports"] == {
        _key(tmp_path, "src/locales/en.ts"),
        _key(tmp_path, "src/locales/fr.ts"),
    }


# ── resolution candidates ────────────────────────────────────


@pytest.mark.parametrize(
    ("specifier", "target"),
    [
        ("./links.mjs", "links.mts"),
        ("./config.cjs", "config.cts"),
        ("./view.jsx", "view.tsx"),
    ],
)
def test_specifier_extensions_map_to_typescript_sources(tmp_path, specifier, target):
    _write(tmp_path, target)
    _write(tmp_path, "main.ts", f"import {{ x }} from '{specifier}';\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "main.ts")]["imports"] == {_key(tmp_path, target)}


def test_directory_import_uses_package_json_entry(tmp_path):
    _write(tmp_path, "vendor/chart/package.json", json.dumps({"types": "./src/chart.ts"}))
    _write(tmp_path, "vendor/chart/src/chart.ts")
    _write(tmp_path, "main.ts", "import { x } from './vendor/chart';\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "main.ts")]["imports"] == {
        _key(tmp_path, "vendor/chart/src/chart.ts")
    }


def test_declaration_files_are_not_sources(tmp_path):
    for name in ("a.ts", "b.mts", "c.cts", "d.tsx", "e.d.ts", "f.d.mts", "g.d.cts"):
        _write(tmp_path, name)
    found = {Path(f).name for f in find_ts_and_tsx_files(tmp_path)}
    assert found == {"a.ts", "b.mts", "c.cts", "d.tsx"}
