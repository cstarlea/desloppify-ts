from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from desloppify.languages.typescript.fixers.if_chain import fix_empty_if_chain


@pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the empty-if-chain fixer needs tree-sitter",
)
def test_same_line_else_chain_is_removed_completely(tmp_path: Path) -> None:
    target = tmp_path / "test.ts"
    target.write_text("if (x) {\n} else {\n}\n", encoding="utf-8")

    result = fix_empty_if_chain(
        [{"file": str(target), "line": 1, "smell_id": "empty_if_chain"}],
        dry_run=False,
    )

    assert result.entries == [
        {
            "file": str(target),
            "removed": ["empty_if_chain"],
            "fixed_issue_ids": [],
            "lines_removed": 3,
        }
    ]
    assert target.read_text(encoding="utf-8") == ""
