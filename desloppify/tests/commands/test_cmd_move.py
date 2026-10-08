"""Tests for generic move command helpers."""

from pathlib import Path
from types import SimpleNamespace

import desloppify.app.commands.move.cmd as move_mod
from desloppify.app.commands.move.language import (
    load_move_module,
    resolve_move_verify_hint,
)
from desloppify.app.commands.move.planning import dedup_replacements, resolve_dest
from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.discovery.file_paths import safe_write_text as safe_write

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------


class TestMoveModuleSanity:
    """Verify move modules import cleanly."""

    def test_move_module_imports(self):
        assert callable(move_mod.cmd_move)


# ---------------------------------------------------------------------------
# dedup_replacements
# ---------------------------------------------------------------------------


class TestDedup:
    """dedup_replacements removes duplicate replacement tuples while preserving order."""

    def test_empty_list(self):
        assert dedup_replacements([]) == []

    def test_no_duplicates(self):
        pairs = [("a", "b"), ("c", "d")]
        assert dedup_replacements(pairs) == pairs

    def test_removes_duplicates(self):
        pairs = [("a", "b"), ("c", "d"), ("a", "b"), ("e", "f"), ("c", "d")]
        assert dedup_replacements(pairs) == [("a", "b"), ("c", "d"), ("e", "f")]

    def test_preserves_order(self):
        pairs = [("z", "y"), ("a", "b"), ("z", "y")]
        assert dedup_replacements(pairs) == [("z", "y"), ("a", "b")]

    def test_different_values_not_deduped(self):
        pairs = [("a", "b"), ("a", "c")]
        assert dedup_replacements(pairs) == [("a", "b"), ("a", "c")]


# ---------------------------------------------------------------------------
# resolve_move_verify_hint
# ---------------------------------------------------------------------------


class TestResolveMoveVerifyHint:
    """resolve_move_verify_hint supports modern move module APIs."""

    def test_prefers_get_verify_hint(self):
        move_mod_api = SimpleNamespace(
            get_verify_hint=lambda: "desloppify detect deps",
            VERIFY_HINT="legacy hint",
        )
        assert resolve_move_verify_hint(move_mod_api) == "desloppify detect deps"

    def test_does_not_use_legacy_constant(self):
        move_mod_api = SimpleNamespace(VERIFY_HINT="npx tsc --noEmit")
        assert resolve_move_verify_hint(move_mod_api) == ""

    def test_returns_empty_when_no_hint_available(self):
        move_mod_api = SimpleNamespace(get_verify_hint=lambda: None)
        assert resolve_move_verify_hint(move_mod_api) == ""


# ---------------------------------------------------------------------------
# resolve_dest
# ---------------------------------------------------------------------------


class TestResolveDest:
    """resolve_dest resolves destination paths."""

    def test_file_to_file(self, tmp_path):
        source = "src/foo.ts"
        dest = str(tmp_path / "bar.ts")
        result = resolve_dest(source, dest, resolve_path)
        assert result.endswith("bar.ts")

    def test_file_to_dir_keeps_filename(self, tmp_path):
        target_dir = tmp_path / "newdir"
        target_dir.mkdir()
        source = "src/foo.ts"
        result = resolve_dest(source, str(target_dir), resolve_path)
        assert result.endswith("foo.ts")
        assert "newdir" in result

    def test_file_to_trailing_slash(self, tmp_path):
        source = "src/foo.ts"
        result = resolve_dest(source, str(tmp_path) + "/", resolve_path)
        assert result.endswith("foo.ts")


class TestLoadMoveModule:
    def test_loads_typescript_move_helpers(self):
        move = load_move_module()
        assert move.__name__ == "desloppify.languages.typescript.move"
        assert callable(move.find_replacements)
        assert callable(move.find_self_replacements)


# ---------------------------------------------------------------------------
# safe_write
# ---------------------------------------------------------------------------


class TestSafeWrite:
    """safe_write performs atomic writes."""

    def test_writes_content(self, tmp_path):
        target = tmp_path / "output.txt"
        safe_write(str(target), "hello world")
        assert target.read_text() == "hello world"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "output.txt"
        target.write_text("old content")
        safe_write(str(target), "new content")
        assert target.read_text() == "new content"

    def test_no_temp_file_left(self, tmp_path):
        target = tmp_path / "output.txt"
        safe_write(str(target), "hello")
        tmp_file = target.with_suffix(".txt.tmp")
        assert not tmp_file.exists()

    def test_string_path_works(self, tmp_path):
        target = str(tmp_path / "string_path.txt")
        safe_write(target, "content")
        assert Path(target).read_text() == "content"

    def test_path_object_works(self, tmp_path):
        target = tmp_path / "path_obj.txt"
        safe_write(target, "content")
        assert target.read_text() == "content"
