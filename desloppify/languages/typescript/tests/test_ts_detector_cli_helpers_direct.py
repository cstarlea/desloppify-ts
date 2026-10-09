"""Direct coverage tests for TS detector helper and CLI modules."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import desloppify.languages.typescript.detectors.smells.helpers as blocks_mod
import desloppify.languages.typescript.detectors.patterns.cli as patterns_cli_mod
import desloppify.languages.typescript.detectors.react.cli as react_cli_mod
from desloppify.languages.typescript.syntax.scanner import SourceText


def test_block_helpers_find_body_and_line_info() -> None:
    lines = [
        "function x() {",
        "  const s = '{not a brace}';",
        "  if (ok) {",
        "    return 1;",
        "  }",
        "}",
    ]
    assert blocks_mod._track_brace_body(lines, 0) == 5

    content = "function x(){\n  const y = 1;\n}\n"
    brace_start = content.index("{")
    end = blocks_mod._find_block_end(content, brace_start)
    assert end is not None
    body = blocks_mod._extract_block_body(content, brace_start)
    assert body is not None and "const y = 1" in body

    line_no, snippet = blocks_mod._content_line_info(content, content.index("const"))
    assert line_no == 2
    assert "const y = 1" in snippet


def test_block_helpers_code_text_strips_comments_and_strings() -> None:
    text = 'const x = "hello"; // comment\nconst y = 1;'
    masked = blocks_mod._code_text(text)
    assert "hello" not in masked
    assert "comment" not in masked
    assert "const y = 1;" in masked


def test_source_text_tells_code_from_comments_and_literals() -> None:
    source = SourceText(
        "\n".join(
            [
                "const a = 1;",
                "/* block",
                "still block",
                "end */",
                "const t = `",
                "inside ${x} template",
                "`; // tail",
            ]
        )
    )
    assert source.kind_at(source.line_starts[2]) == "comment"
    assert source.kind_at(source.line_starts[5]) == "template"
    assert source.kind_at(source.line_starts[5] + len("inside ${")) is None
    assert source.code_lines[6] == " ;" + " " * 8
    assert [i for i, _ in source.line_matches(r"//", "comment")] == [6]
    assert [i for i, _ in source.line_matches(r"`", "literal")] == [4]


def test_patterns_cli_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        patterns_cli_mod,
        "_build_census",
        lambda _path: (
            {"ui": {"state": {"redux"}}},
            {},
        ),
    )
    monkeypatch.setattr(
        patterns_cli_mod,
        "detect_pattern_anomalies",
        lambda _path: SimpleNamespace(
            entries=[
                {
                    "area": "ui",
                    "family": "state",
                    "confidence": "medium",
                    "patterns_used": ["redux"],
                }
            ]
        ),
    )
    args = argparse.Namespace(path=".", json=True, top=5)
    patterns_cli_mod.cmd_patterns(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["areas"] == 1
    assert payload["anomalies"] == 1
    assert "census" in payload


def test_react_cli_json_and_empty_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        react_cli_mod,
        "detect_state_sync",
        lambda _path: ([{"file": "/repo/src/a.tsx", "line": 10, "setters": ["setX"]}], {}),
    )
    monkeypatch.setattr(react_cli_mod, "rel", lambda p: str(p).split("/repo/")[-1])
    args = argparse.Namespace(path=".", json=True, top=5)
    react_cli_mod.cmd_react(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["entries"][0]["file"] == "src/a.tsx"

    monkeypatch.setattr(react_cli_mod, "detect_state_sync", lambda _path: ([], {}))
    args_plain = argparse.Namespace(path=".", json=False, top=5)
    react_cli_mod.cmd_react(args_plain)
    assert "No state sync anti-patterns found." in capsys.readouterr().out
