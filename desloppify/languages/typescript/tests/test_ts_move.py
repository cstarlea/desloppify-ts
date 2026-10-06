"""Tests for TypeScript move helpers."""

from __future__ import annotations

import pytest

import desloppify.languages.typescript.move as ts_move


def test_move_ts_module_imports():
    assert callable(ts_move.find_replacements)
    assert callable(ts_move.find_self_replacements)


class TestMoveTsHelpers:
    def test_strip_ts_ext(self):
        assert ts_move._strip_ts_ext("foo.ts") == "foo"
        assert ts_move._strip_ts_ext("foo.tsx") == "foo"
        assert ts_move._strip_ts_ext("foo.js") == "foo"
        assert ts_move._strip_ts_ext("foo.jsx") == "foo"
        assert ts_move._strip_ts_ext("foo") == "foo"
        assert ts_move._strip_ts_ext("foo.css") == "foo.css"

    def test_compute_ts_specifiers_relative(self):
        alias, relative = ts_move._compute_ts_specifiers(
            "/project/src/a.ts", "/project/src/b.ts"
        )
        assert relative == "./b"
        assert alias is None

    def test_compute_ts_specifiers_parent(self):
        alias, relative = ts_move._compute_ts_specifiers(
            "/project/src/sub/a.ts", "/project/src/b.ts"
        )
        assert relative == "../b"
        assert alias is None

    def test_strip_index_from_relative(self):
        alias, relative = ts_move._compute_ts_specifiers(
            "/project/src/a.ts",
            "/project/src/utils/index.ts",
        )
        assert relative == "./utils"
        assert not relative.endswith("/index")
        assert alias is None


class TestMoveSafety:
    """Directory moves, ESM specifiers and the unrewritable-importer gate."""

    def test_esm_js_specifiers_are_rewritten(self):
        from desloppify.languages.typescript.move import _quoted_replacements

        content = "import { x } from './feature/x.js';\nimport y from \"./feature/x\";\n"
        assert _quoted_replacements(content, "./feature/x", "./mod/x") == [
            ('"./feature/x"', '"./mod/x"'),
            ("'./feature/x.js'", "'./mod/x.js'"),
        ]

    def test_intra_package_relative_rewrites_are_dropped(self):
        from desloppify.languages.typescript.move import filter_intra_package_importer_changes

        replacements = [("'./y'", "'../../new/y'"), ("'@/feature/y'", "'@/new/y'")]
        kept = filter_intra_package_importer_changes("/p/src/feature/x.ts", replacements, set())
        assert kept == [("'@/feature/y'", "'@/new/y'")]

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
