"""A published package's public API is not reported as unused exports."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import desloppify.languages.typescript.detectors.exports as exports_mod
from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors.deps.public_api import (
    public_export_names,
)
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.syntax.tree import get_parser

pytestmark = pytest.mark.skipif(get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar")


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _library(root: Path, *, private: bool = False) -> None:
    manifest = {"name": "lib", "type": "module", "exports": {".": "./dist/index.js"}}
    if private:
        manifest["private"] = True
    _write(root, "package.json", json.dumps(manifest))
    _write(root, "tsconfig.json", json.dumps({"compilerOptions": {"outDir": "dist", "rootDir": "src"}}))
    _write(
        root,
        "src/index.ts",
        "export { request as fetchIt } from './request.js';\n"
        "export * from './errors.js';\n"
        "export * as hooks from './hooks.js';\n"
        "export type { Options } from './types.js';\n"
        "import { Method } from './method.js';\n"
        "export { Method };\n",
    )
    _write(root, "src/request.ts", "export function request() {}\nexport function internalHelper() {}\n")
    _write(root, "src/errors.ts", "export class HttpError {}\nexport default 1;\n")
    _write(root, "src/hooks.ts", "export const before = 1;\nexport default 2;\n")
    _write(root, "src/types.ts", "export type Options = {};\nexport type Hidden = {};\n")
    _write(root, "src/method.ts", "export enum Method { Get = 'GET', Purge = 'PURGE' }\n")


@pytest.fixture
def scope(tmp_path):
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        clear_source_file_cache_for_tests()
        clear_resolver_cache()
        yield tmp_path
        clear_resolver_cache()
        clear_source_file_cache_for_tests()


def _candidates(root: Path) -> list[str]:
    return sorted(str(p) for p in (root / "src").glob("*.ts"))


def test_public_api_follows_reexport_chains(scope):
    root = scope
    _library(root)
    public = public_export_names(_candidates(root), root)
    assert {
        ("src/index.ts", "fetchIt"),
        ("src/request.ts", "request"),
        ("src/errors.ts", "HttpError"),
        ("src/hooks.ts", "before"),
        ("src/hooks.ts", "default"),
        ("src/types.ts", "Options"),
        ("src/method.ts", "Method"),
    } <= public
    # Not re-exported, and ``export *`` doesn't forward a default export.
    assert not {
        ("src/request.ts", "internalHelper"),
        ("src/types.ts", "Hidden"),
        ("src/errors.ts", "default"),
    } & public


def test_private_package_publishes_nothing(scope):
    root = scope
    _library(root, private=True)
    assert public_export_names(_candidates(root), root) == set()


def test_unused_exports_keep_internal_ones_only(scope):
    root = scope
    _library(root)
    knip = [
        {"file": "src/request.ts", "name": "request", "line": 1, "kind": "export"},
        {"file": "src/request.ts", "name": "internalHelper", "line": 2, "kind": "export"},
        {"file": "src/types.ts", "name": "Options", "line": 1, "kind": "type"},
        {"file": "src/types.ts", "name": "Hidden", "line": 2, "kind": "type"},
        {"file": "src/method.ts", "name": "Method.Purge", "line": 1, "kind": "enum_member"},
        {"file": "src/errors.ts", "name": "HttpError=Alias", "line": 1, "kind": "duplicate", "names": []},
    ]
    with patch.object(exports_mod, "detect_with_knip_result", return_value=(knip, None)):
        entries, _total, _coverage = exports_mod.detect_dead_exports_result(root)
    assert [(e["file"], e["name"]) for e in entries] == [
        ("src/request.ts", "internalHelper"),
        ("src/types.ts", "Hidden"),
        ("src/errors.ts", "HttpError=Alias"),
    ]
