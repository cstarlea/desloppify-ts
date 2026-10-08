"""TypeScript test-coverage hooks: import parsing and package-scoped name mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
import desloppify.languages.typescript.test_coverage as ts_coverage_mod
from desloppify.engine.detectors.coverage.mapping import import_based_mapping, naming_based_mapping
from desloppify.languages.typescript.detectors.deps.imports import ImportExtractor
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.test_coverage import (
    has_testable_logic,
    imported_definitions,
    map_test_to_source,
    parse_test_import_specs,
)

needs_treesitter = pytest.mark.skipif(
    not ImportExtractor().uses_treesitter, reason="needs tree-sitter with the tsx grammar"
)


@pytest.fixture(autouse=True)
def _root(set_project_root):
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    yield
    clear_resolver_cache()


def _touch(root: Path, name: str, content: str = "") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return name


@pytest.mark.skipif(
    not ImportExtractor().uses_treesitter, reason="needs tree-sitter with the tsx grammar"
)
def test_import_specs_skip_comments_strings_and_mocks():
    content = (
        "import { a } from './a';\n"
        "// import { b } from './b';\n"
        "const c = \"import { c } from './c'\";\n"
        "vi.mock('./d');\n"
        "const e = await import('./e.js');\n"
    )
    assert parse_test_import_specs(content) == ["./a", "./e.js"]


def test_same_named_file_in_another_package_is_not_mapped(tmp_path):
    _touch(tmp_path, "packages/a/package.json", "{}")
    _touch(tmp_path, "packages/b/package.json", "{}")
    test = _touch(tmp_path, "packages/a/test/format.test.ts")
    other = _touch(tmp_path, "packages/b/src/format.ts")
    assert map_test_to_source(test, {other}) is None
    assert naming_based_mapping({test}, {other}, "typescript") == set()


def test_same_named_file_in_the_same_package_is_mapped(tmp_path):
    _touch(tmp_path, "packages/a/package.json", "{}")
    _touch(tmp_path, "packages/b/package.json", "{}")
    test = _touch(tmp_path, "packages/a/test/format.test.ts")
    own = _touch(tmp_path, "packages/a/src/format.ts")
    other = _touch(tmp_path, "packages/b/src/format.ts")
    assert map_test_to_source(test, {own, other}) == own


def test_closest_same_named_file_wins(tmp_path):
    test = _touch(tmp_path, "src/features/cart/__tests__/types.test.ts")
    near = _touch(tmp_path, "src/features/cart/model/types.ts")
    far = _touch(tmp_path, "src/lib/types.ts")
    assert map_test_to_source(test, {near, far}) == near


# ── re-export chains ────────────────────────────────────────


def _chain_project(root: Path) -> dict[str, str]:
    """pkg/index -> export * -> api -> export { } from -> inner/index -> import-then-export -> impl."""
    return {
        "index": _touch(root, "src/index.ts", "export * from './api';\nexport * from './other';\n"),
        "api": _touch(root, "src/api.ts", "export { parse } from './inner';\nexport type { Shape } from './shape';\n"),
        "inner": _touch(root, "src/inner/index.ts", "import { parse as p } from './impl';\nexport { p as parse };\n"),
        "impl": _touch(root, "src/inner/impl.ts", "export function parse(s: string) {\n  return s.trim();\n}\n"),
        "shape": _touch(root, "src/shape.ts", "export const Shape = {};\nexport type Shape = {};\n"),
        "other": _touch(root, "src/other.ts", "export function other() {\n  return 1;\n}\n"),
    }


@needs_treesitter
def test_imported_name_is_credited_to_its_definition_through_any_depth(tmp_path):
    files = _chain_project(tmp_path)
    test = _touch(tmp_path, "test/parse.test.ts", "import { parse } from '../src';\nparse(' a ');\n")
    production = set(files.values())
    assert imported_definitions(test, production) == {files["impl"]}
    assert files["impl"] in import_based_mapping({}, {test}, production, "typescript")


@needs_treesitter
def test_barrel_siblings_of_an_imported_name_are_not_credited(tmp_path, monkeypatch):
    files = _chain_project(tmp_path)
    test = _touch(tmp_path, "test/parse.test.ts", "import { parse } from '../src';\nparse(' a ');\n")
    production = set(files.values())
    graph = {test: {"imports": {files["index"]}}, files["index"]: {"imports": {files["api"], files["other"]}}}
    tested = import_based_mapping(graph, {test}, production, "typescript")
    assert files["impl"] in tested
    assert files["other"] not in tested

    # Without tree-sitter the name-blind barrel and facade hops remain the fallback.
    monkeypatch.setattr(ts_coverage_mod, "follows_reexport_names", lambda: False)
    assert files["other"] in import_based_mapping(graph, {test}, production, "typescript")


@needs_treesitter
def test_type_only_imports_and_exports_are_not_followed(tmp_path):
    files = _chain_project(tmp_path)
    test = _touch(
        tmp_path,
        "test/shape.test.ts",
        "import type { parse } from '../src';\nimport { type Shape } from '../src/api';\n",
    )
    assert imported_definitions(test, set(files.values())) == set()


@needs_treesitter
def test_namespace_import_follows_the_members_used(tmp_path):
    files = {
        "index": _touch(tmp_path, "src/index.ts", "export * as core from './core';\nexport * from './schemas';\n"),
        "core": _touch(tmp_path, "src/core.ts", "export * from './checks';\n"),
        "checks": _touch(tmp_path, "src/checks.ts", "export function minLength() {\n  return 1;\n}\n"),
        "schemas": _touch(tmp_path, "src/schemas.ts", "export function string() {\n  return 1;\n}\n"),
        "unused": _touch(tmp_path, "src/unused.ts", "export function unused() {\n  return 1;\n}\n"),
    }
    test = _touch(
        tmp_path,
        "test/z.test.ts",
        "import * as z from '../src';\nz.string();\nz.core.minLength().toString();\n",
    )
    assert imported_definitions(test, set(files.values())) == {files["schemas"], files["checks"]}


@needs_treesitter
def test_anonymous_default_export_is_a_definition(tmp_path):
    files = {
        "index": _touch(tmp_path, "src/index.ts", "export * as locales from './locales';\n"),
        "locales": _touch(tmp_path, "src/locales.ts", "export { default as ka } from './ka';\nexport { default as ro } from './ro';\n"),
        "ka": _touch(tmp_path, "src/ka.ts", "export default function () {\n  return 1;\n}\n"),
        "ro": _touch(tmp_path, "src/ro.ts", "export default { ro: true };\n"),
    }
    test = _touch(tmp_path, "test/ka.test.ts", "import * as z from '../src';\nz.locales.ka();\nz.locales.ro;\n")
    assert imported_definitions(test, set(files.values())) == {files["ka"], files["ro"]}


@needs_treesitter
def test_star_export_cycles_terminate(tmp_path):
    a = _touch(tmp_path, "src/a.ts", "export * from './b';\n")
    b = _touch(tmp_path, "src/b.ts", "export * from './a';\n")
    test = _touch(tmp_path, "test/a.test.ts", "import { missing } from '../src/a';\n")
    assert imported_definitions(test, {a, b}) == set()


# ── testable logic ──────────────────────────────────────────


@needs_treesitter
@pytest.mark.parametrize(
    "content",
    [
        # zod's json-schema.ts: a multi-line union, then a commented-out interface
        "export type Schema =\n  | ObjectSchema\n  | ArraySchema;\n\n// export interface JSONSchema {\n//   type?: string;\n// }\n",
        "export type Pick2<T> =\n  T extends string\n    ? 'a'\n    : 'b';\n",
        "import type { A } from './a';\nexport default interface B extends A {\n  b: string;\n}\n",
        "'use client';\nexport type { A } from './a';\nexport {};\ndeclare const x: number;\n",
    ],
)
def test_type_only_files_have_no_testable_logic(content):
    assert has_testable_logic("src/types.ts", content) is False


@needs_treesitter
@pytest.mark.parametrize(
    "content",
    [
        "export type A =\n  | 'a'\n  | 'b';\nexport const a: A = 'a';\n",
        "export enum Color {\n  Red,\n}\n",
        "export default {\n  a: 1,\n};\n",
        "export default function () {\n  return 1;\n}\n",
        "const a = 1;\nexport { a };\n",
    ],
)
def test_runtime_statements_are_testable_logic(content):
    assert has_testable_logic("src/mod.ts", content) is True


def test_line_heuristic_without_treesitter(monkeypatch):
    monkeypatch.setattr(ts_coverage_mod, "parse_text", lambda *_args: None)
    assert has_testable_logic("src/a.ts", "export type A = { a: 1 };\n") is False
    assert has_testable_logic("src/a.ts", "export const a = 1;\n") is True
