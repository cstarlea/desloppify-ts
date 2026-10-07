"""Output parsers for external tools run by framework phases."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


class ToolParserError(ValueError):
    """Raised when a parser cannot decode tool output for its declared format."""


def _load_json_output(output: str, *, parser_name: str) -> object:
    """Decode JSON output or raise a typed parser error."""
    try:
        return json.loads(output)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ToolParserError(
            f"{parser_name} parser could not decode JSON output"
        ) from exc


def _coerce_line(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _extract_json_array(text: str) -> str | None:
    """Best-effort: return the first JSON array substring in *text*."""
    start = text.find("[")
    if start == -1:
        return None
    end = text.rfind("]")
    if end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _relativize_to_project_root(filepath: str, *, scan_path: Path) -> str:
    """Resolve a tool-emitted path to a project-root-relative string when possible."""
    from desloppify.base.discovery.paths import get_project_root

    project_root = get_project_root().resolve()
    try:
        p = Path(filepath)
        abs_path = p.resolve() if p.is_absolute() else (scan_path / p).resolve()
        try:
            return str(abs_path.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            return str(abs_path)
    except Exception:  # pragma: no cover
        return filepath


def parse_next_lint(output: str, scan_path: Path) -> tuple[list[dict], dict]:
    """Parse Next.js `next lint --format json` output.

    Returns ``(entries, meta)`` where:
    - entries are *per-file* aggregates: {file, line, message, id, detail}
    - meta includes ``potential`` (number of files lint reported on)
    """
    raw = (output or "").strip()
    json_text = _extract_json_array(raw)
    if not json_text:
        raise ToolParserError("next_lint parser could not find JSON output array")

    data = _load_json_output(json_text, parser_name="next_lint")
    if not isinstance(data, list):
        raise ToolParserError("next_lint parser expected a JSON array")

    potential = len(data)
    entries: list[dict] = []
    for fobj in data:
        if not isinstance(fobj, dict):
            continue
        file_path = fobj.get("filePath") or ""
        messages = fobj.get("messages") or []
        if not file_path or not isinstance(messages, list) or not messages:
            continue

        rel = _relativize_to_project_root(str(file_path), scan_path=scan_path)
        first = next((m for m in messages if isinstance(m, dict)), None)
        if first is None:
            continue
        line = _coerce_line(first.get("line", 0)) or 1
        msg = first.get("message") if isinstance(first.get("message"), str) else "Lint issue"
        entries.append(
            {
                "file": rel,
                "line": line if line > 0 else 1,
                "id": "lint",
                "message": f"next lint: {msg} ({len(messages)} issue(s) in file)",
                "detail": {
                    "count": len(messages),
                    "messages": [
                        {
                            "line": _coerce_line(m.get("line", 0)) or 0,
                            "column": _coerce_line(m.get("column", 0)) or 0,
                            "ruleId": m.get("ruleId", "") if isinstance(m.get("ruleId", ""), str) else "",
                            "message": m.get("message", "") if isinstance(m.get("message", ""), str) else "",
                            "severity": _coerce_line(m.get("severity", 0)) or 0,
                        }
                        for m in messages
                        if isinstance(m, dict)
                    ][:50],
                },
            }
        )

    return entries, {"potential": potential}


ToolParseResult = list[dict] | tuple[list[dict], dict]
ToolParser = Callable[[str, Path], ToolParseResult]


PARSERS: dict[str, ToolParser] = {
    "next_lint": parse_next_lint,
}


__all__ = [
    "PARSERS",
    "ToolParserError",
    "ToolParseResult",
    "ToolParser",
    "parse_next_lint",
]
