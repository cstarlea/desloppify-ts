"""TypeScript fixer tests for if-chain and dead-useEffect cleanup."""

import importlib.util
import textwrap

import pytest

from desloppify.languages.typescript.fixers.if_chain import fix_empty_if_chain
from desloppify.languages.typescript.fixers.useeffect import fix_dead_useeffect


# =====================================================================
# if_chain.py — fix_empty_if_chain
# =====================================================================


@pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the empty-if-chain fixer needs tree-sitter",
)
class TestFixEmptyIfChain:
    """Tests for fix_empty_if_chain()."""

    def test_remove_empty_if(self, tmp_path):
        """An empty if block is removed."""
        ts_file = tmp_path / "logic.ts"
        ts_file.write_text(
            textwrap.dedent("""\
            if (x) {
            }
            const y = 1;
        """)
        )
        entries = [{"file": str(ts_file), "line": 1, "content": "if (x) {"}]
        result = fix_empty_if_chain(entries, dry_run=False)
        assert len(result.entries) == 1
        content = ts_file.read_text()
        assert "if (x)" not in content
        assert "const y = 1;" in content

    def test_dry_run(self, tmp_path):
        """dry_run=True does not modify the file."""
        ts_file = tmp_path / "logic.ts"
        original = "if (x) {\n}\nconst y = 1;\n"
        ts_file.write_text(original)
        entries = [{"file": str(ts_file), "line": 1, "content": "if (x) {"}]
        result = fix_empty_if_chain(entries, dry_run=True)
        assert len(result.entries) == 1
        assert ts_file.read_text() == original


# =====================================================================
# useeffect.py — fix_dead_useeffect
# =====================================================================


class TestFixDeadUseEffect:
    """Tests for fix_dead_useeffect()."""

    def test_remove_empty_useeffect(self, tmp_path):
        """An empty useEffect call is removed."""
        ts_file = tmp_path / "comp.tsx"
        ts_file.write_text(
            textwrap.dedent("""\
            useEffect(() => {
            }, []);
            const x = 1;
        """)
        )
        entries = [{"file": str(ts_file), "line": 1, "content": "useEffect(() => {"}]
        result = fix_dead_useeffect(entries, dry_run=False)
        assert len(result.entries) == 1
        content = ts_file.read_text()
        assert "useEffect" not in content
        assert "const x = 1;" in content

    def test_removes_preceding_comment(self, tmp_path):
        """A comment immediately before the useEffect is also removed."""
        ts_file = tmp_path / "comp.tsx"
        ts_file.write_text(
            textwrap.dedent("""\
            // Load data on mount
            useEffect(() => {
            }, []);
            const x = 1;
        """)
        )
        entries = [{"file": str(ts_file), "line": 2, "content": "useEffect(() => {"}]
        _ = fix_dead_useeffect(entries, dry_run=False)
        content = ts_file.read_text()
        assert "Load data" not in content
        assert "useEffect" not in content

    def test_dry_run(self, tmp_path):
        """dry_run=True does not modify the file."""
        ts_file = tmp_path / "comp.tsx"
        original = "useEffect(() => {\n}, []);\nconst x = 1;\n"
        ts_file.write_text(original)
        entries = [{"file": str(ts_file), "line": 1, "content": "useEffect(() => {"}]
        assert len(fix_dead_useeffect(entries, dry_run=True).entries) == 1
        assert ts_file.read_text() == original
