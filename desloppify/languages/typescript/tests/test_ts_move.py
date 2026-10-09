"""Tests for TypeScript move helpers."""

from __future__ import annotations

import json
from pathlib import Path

import sys

import pytest

import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
import desloppify.languages.typescript.move as ts_move
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache


class TestMoveTsHelpers:
    def test_strip_ts_ext(self):
        assert ts_move._strip_ts_ext("foo.ts") == "foo"
        assert ts_move._strip_ts_ext("foo.tsx") == "foo"
        assert ts_move._strip_ts_ext("foo.js") == "foo"
        assert ts_move._strip_ts_ext("foo.jsx") == "foo"
        assert ts_move._strip_ts_ext("foo") == "foo"
        assert ts_move._strip_ts_ext("foo.css") == "foo.css"


class TestMoveSafety:
    """Directory moves, ESM specifiers and the unrewritable-importer gate."""

    def test_intra_package_relative_rewrites_are_dropped(self):
        from desloppify.languages.typescript.move import filter_intra_package_importer_changes

        replacements = [("'./y'", "'../../new/y'"), ("'@/feature/y'", "'@/new/y'")]
        kept = filter_intra_package_importer_changes("/p/src/feature/x.ts", replacements, set())
        assert kept == [("'@/feature/y'", "'@/new/y'")]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX absolute paths")
    def test_self_rewrites_to_files_moving_together_are_dropped(self):
        from desloppify.languages.typescript.move import filter_directory_self_changes

        moving = {"/p/src/feature/x.ts", "/p/src/feature/y.ts", "/p/src/feature/sub/index.ts"}
        changes = [
            ("'./y'", "'../../feature/y'"),  # sibling, moving too
            ("'./y.js'", "'../../feature/y.js'"),
            ("'./sub'", "'../../feature/sub'"),  # directory index, moving too
            ("'../shared/util'", "'../../shared/util'"),  # stays put: must be rewritten
        ]
        kept = filter_directory_self_changes("/p/src/feature/x.ts", changes, moving)
        assert kept == [("'../shared/util'", "'../../shared/util'")]

    def test_apply_replacements_is_single_pass(self):
        from desloppify.app.commands.move.planning import apply_replacements

        content = "import a from './a';\nimport b from './b';\n"
        result = apply_replacements(content, [("'./a'", "'./b'"), ("'./b'", "'./c'")])
        assert result == "import a from './b';\nimport b from './c';\n"

    def test_unrewritable_importers_abort_strict_languages(self):
        from types import SimpleNamespace

        from desloppify.app.commands.move.planning import (
            check_unrewritable_importers,
            find_unrewritable_importers,
        )
        from desloppify.base.exception_sets import CommandError

        graph = {
            "/p/src/y.ts": {"importers": {"/p/src/x.ts", "/p/src/z.ts", "/p/src/y_sib.ts"}},
        }
        moving = {"/p/src/y.ts", "/p/src/y_sib.ts"}
        assert find_unrewritable_importers(graph, moving, {"/p/src/x.ts"}) == {
            "/p/src/z.ts": ["/p/src/y.ts"]
        }

        strict = SimpleNamespace(REQUIRES_ALL_IMPORTERS_REWRITTEN=True)
        warnings: list[str] = []
        kwargs = dict(rel_fn=lambda p: p, warn_fn=warnings.append)
        with pytest.raises(CommandError, match="z.ts"):
            check_unrewritable_importers(
                strict, graph, moving, {"/p/src/x.ts"}, dry_run=False, force=False, **kwargs
            )
        check_unrewritable_importers(
            strict, graph, moving, {"/p/src/x.ts"}, dry_run=True, force=False, **kwargs
        )
        check_unrewritable_importers(
            strict, graph, moving, {"/p/src/x.ts"}, dry_run=False, force=True, **kwargs
        )
        assert len(warnings) == 2

        # Languages whose imports don't depend on file location (C#) never abort.
        check_unrewritable_importers(
            SimpleNamespace(), graph, moving, set(), dry_run=False, force=False, **kwargs
        )


# ── replacements computed with the shared resolver ──────────


@pytest.fixture
def project(tmp_path, set_project_root):
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    yield tmp_path
    clear_resolver_cache()


def _write(root: Path, name: str, content: str = "export const x = 1;\n") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path.resolve())


def _replacements(root: Path, source: str, dest: str):
    graph = deps_detector_mod.build_dep_graph(root)
    src, dst = str((root / source).resolve()), str((root / dest).resolve())
    return (
        ts_move.find_replacements(src, dst, graph),
        ts_move.find_self_replacements(src, dst, graph),
    )


class TestFindReplacements:
    def test_relative_specifiers_keep_their_style(self, project):
        _write(project, "src/lib/format.ts")
        esm = _write(project, "src/a.ts", "import { x } from './lib/format.js';\n")
        plain = _write(project, "src/views/b.ts", 'import { x } from "../lib/format";\n')
        changes, _ = _replacements(project, "src/lib/format.ts", "src/util/text.ts")
        assert changes == {
            esm: [("'./lib/format.js'", "'./util/text.js'")],
            plain: [('"../lib/format"', '"../util/text"')],
        }

    def test_directory_index_import_stays_a_directory_import(self, project):
        _write(project, "src/widgets/index.ts")
        importer = _write(project, "src/main.ts", "import { x } from './widgets';\n")
        changes, _ = _replacements(project, "src/widgets/index.ts", "src/ui/index.ts")
        assert changes == {importer: [("'./widgets'", "'./ui'")]}

    def test_tsconfig_alias_is_kept_when_it_covers_the_destination(self, project):
        _write(project, "tsconfig.json", json.dumps({"compilerOptions": {"paths": {"~/*": ["./app/*"]}}}))
        _write(project, "app/lib/format.ts")
        importer = _write(project, "app/page.ts", "import { x } from '~/lib/format';\n")
        changes, _ = _replacements(project, "app/lib/format.ts", "app/util/text.ts")
        assert changes == {importer: [("'~/lib/format'", "'~/util/text'")]}

    def test_alias_falls_back_to_relative_outside_its_directory(self, project):
        _write(project, "tsconfig.json", json.dumps({"compilerOptions": {"paths": {"~/*": ["./app/*"]}}}))
        _write(project, "app/lib/format.ts")
        importer = _write(project, "app/page.ts", "import { x } from '~/lib/format';\n")
        changes, _ = _replacements(project, "app/lib/format.ts", "shared/format.ts")
        assert changes == {importer: [("'~/lib/format'", "'../shared/format'")]}

    def test_workspace_package_import_is_not_rewritten(self, project):
        _write(project, "package.json", json.dumps({"name": "root", "workspaces": ["packages/*"]}))
        _write(project, "packages/ui/package.json", json.dumps({"name": "@acme/ui", "exports": "./src/index.ts"}))
        _write(project, "packages/ui/src/index.ts")
        _write(project, "packages/app/package.json", json.dumps({"name": "app"}))
        _write(project, "packages/app/main.ts", "import { x } from '@acme/ui';\n")
        changes, _ = _replacements(project, "packages/ui/src/index.ts", "packages/ui/src/main.ts")
        assert changes == {}

    def test_commented_out_import_is_not_an_importer_specifier(self, project):
        _write(project, "src/old.ts")
        _write(project, "src/new.ts")
        importer = _write(
            project,
            "src/main.ts",
            "// import { x } from './old';\nimport { x } from './new';\n",
        )
        changes, _ = _replacements(project, "src/new.ts", "src/lib/new.ts")
        assert changes == {importer: [("'./new'", "'./lib/new'")]}

    def test_self_imports_are_rebased(self, project):
        _write(project, "src/lib/format.ts")
        _write(project, "src/lib/util/index.ts")
        _write(
            project,
            "src/lib/report.ts",
            "import { x } from './format.js';\nimport { y } from './util';\nimport z from 'react';\n",
        )
        _, self_changes = _replacements(project, "src/lib/report.ts", "src/features/report.ts")
        assert self_changes == [
            ("'./format.js'", "'../lib/format.js'"),
            ("'./util'", "'../lib/util'"),
        ]
