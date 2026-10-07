"""TypeScript test-coverage hooks: import parsing and package-scoped name mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.engine.detectors.coverage.mapping import naming_based_mapping
from desloppify.languages.typescript.detectors.deps.imports import ImportExtractor
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.test_coverage import (
    map_test_to_source,
    parse_test_import_specs,
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
