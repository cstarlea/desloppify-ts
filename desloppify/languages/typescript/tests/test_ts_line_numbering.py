"""One line rule for every detector and fixer: tsc's (roadmap 2.29).

LF, CR, CRLF, U+2028 and U+2029 end a line; VT, FF and U+0085, which
``str.splitlines`` also breaks at, don't.
"""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.languages.typescript.detectors.logs import detect_logs
from desloppify.languages.typescript.detectors.security.detector import (
    detect_ts_security,
)
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.syntax.lines import (
    byte_line_starts,
    line_number,
    line_starts,
    split_lines,
)
from desloppify.languages.typescript.syntax.nodes import byte_offset
from desloppify.languages.typescript.syntax.queries import calls
from desloppify.languages.typescript.syntax.scanner import SourceText

HAS_TREESITTER = importlib.util.find_spec("tree_sitter_language_pack") is not None

BREAKS = {"LS": " ", "PS": " ", "CR": "\r", "CRLF": "\r\n", "LF": "\n"}
NOT_BREAKS = {"VT": "\x0b", "FF": "\x0c", "NEL": "\x85"}


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


@pytest.fixture(params=["tree", "fallback"])
def mode(request, monkeypatch):
    if request.param == "tree" and not HAS_TREESITTER:
        pytest.skip("needs tree-sitter")
    if request.param == "fallback":
        monkeypatch.setattr(tree_mod, "get_parser", lambda grammar: None)
    return request.param


@pytest.mark.parametrize("char", BREAKS.values(), ids=BREAKS.keys())
def test_javascript_breaks_end_a_line(char):
    text = f"a{char}b\nc\n"
    assert split_lines(text) == ["a", "b", "c"]
    assert line_starts(text) == [0, 1 + len(char), 3 + len(char)]
    assert line_number(text, text.index("c")) == 3


@pytest.mark.parametrize("char", NOT_BREAKS.values(), ids=NOT_BREAKS.keys())
def test_other_splitlines_breaks_do_not(char):
    text = f"a{char}b\nc\n"
    assert split_lines(text) == [f"a{char}b", "c"]
    assert line_number(text, text.index("c")) == 2
    assert byte_line_starts(text.encode()) is None


@pytest.mark.skipif(not HAS_TREESITTER, reason="needs tree-sitter")
@pytest.mark.parametrize(
    "char", [*BREAKS.values(), *NOT_BREAKS.values()], ids=[*BREAKS, *NOT_BREAKS]
)
def test_tree_lines_match_tsc_positions(char):
    in_string = "" if char in ("\r", "\r\n", "\n") else char
    source = f"const s = 'x'; /* a{char}b */ const t = 'y{in_string}z';\nf();\n"
    parsed = tree_mod.parse_text(source, "a.ts")
    [call] = calls(parsed)
    line = parsed.line(call.node)
    assert line == len(split_lines(source))
    assert byte_offset(parsed.source, line, 1) == call.node.start_byte
    assert parsed.line_text(call.node) == "f();"


def _file(char: str) -> str:
    """A string and a comment holding ``char``, then one finding of each kind per line."""
    in_string = char if char not in ("\r", "\r\n", "\n") else ""
    return (
        f"const s = 'a{in_string}b'; /* c{char}d */\n"
        "try { a() } catch (e) {}\n"
        "const n = x > 99999;\n"
        "console.log('[Tag] here');\n"
        "eval(code);\n"
    )


@pytest.mark.parametrize(
    "char", [*BREAKS.values(), *NOT_BREAKS.values()], ids=[*BREAKS, *NOT_BREAKS]
)
def test_detectors_report_tsc_lines(tmp_path, mode, char):
    content = _file(char)
    (tmp_path / "a.ts").write_bytes(content.encode())
    first = SourceText(content).lines.index("try { a() } catch (e) {}") + 1

    entries, _ = detect_smells(tmp_path)
    lines = {e["id"]: [m["line"] for m in e["matches"]] for e in entries}
    assert lines["empty_catch"] == [first]
    assert lines["magic_number"] == [first + 1]
    assert [e["line"] for e in detect_logs(tmp_path).entries] == [first + 2]
    security = detect_ts_security([str(tmp_path / "a.ts")], None).entries
    assert [e["detail"]["line"] for e in security] == [first + 3]
    # In the string and the comment, U+2028 and U+2029 break twice; CR and LF only fit in the comment.
    breaks = {"\u2028": 2, "\u2029": 2, "\r": 1, "\r\n": 1, "\n": 1}.get(char, 0)
    assert first == 2 + breaks


@pytest.mark.parametrize("char", BREAKS.values(), ids=BREAKS.keys())
def test_empty_match_on_an_empty_line_ends(char):
    """An empty match that isn't anchored used to retry the end of an empty line forever."""
    assert list(SourceText(f"{char}x").line_matches("", "comment")) == []
