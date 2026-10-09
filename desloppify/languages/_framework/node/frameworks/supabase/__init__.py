"""Supabase support: Edge Function entries and checks over SQL migrations.

Supabase serves every table and view in the ``public`` schema through its
Data API, so the anon key reaches whatever row-level security (RLS) doesn't
stop. The checks read ``supabase/migrations`` (and declarative
``supabase/schemas``) in order, the way the database applies them, and mirror
Supabase's own advisor lints 0013 (``rls_disabled_in_public``) and 0010
(``security_definer_view``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import rel

SUPABASE_DIR = "supabase"
FUNCTIONS_DIR = f"{SUPABASE_DIR}/functions"
_SQL_DIRS = (f"{SUPABASE_DIR}/migrations", f"{SUPABASE_DIR}/schemas")

_IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
_QNAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"
_NAME = f"({_QNAME})"
_CREATE_TABLE_RE = re.compile(
    rf"\bcreate\s+(?:(?:global\s+|local\s+)?(?:temp|temporary|unlogged)\s+)?table\s+"
    rf"(?:if\s+not\s+exists\s+)?{_NAME}",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(r"\b(?:temp|temporary)\b", re.IGNORECASE)
_ENABLE_RLS_RE = re.compile(
    rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_NAME}\s+"
    r"(?:enable|force)\s+row\s+level\s+security",
    re.IGNORECASE,
)
_DISABLE_RLS_RE = re.compile(
    rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_NAME}\s+"
    r"disable\s+row\s+level\s+security",
    re.IGNORECASE,
)
_RENAME_TABLE_RE = re.compile(
    rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_NAME}\s+rename\s+to\s+({_IDENT})",
    re.IGNORECASE,
)
_DROP_RE = re.compile(
    r"\bdrop\s+(table|view)\s+(?:if\s+exists\s+)?"
    rf"({_QNAME}(?:\s*,\s*{_QNAME})*)",
    re.IGNORECASE,
)
_CREATE_VIEW_RE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?(?:(?:temp|temporary)\s+)?(?:recursive\s+)?"
    rf"view\s+(?:if\s+not\s+exists\s+)?{_NAME}",
    re.IGNORECASE,
)
_VIEW_OPTIONS_RE = re.compile(r"\bwith\s*\(([^)]*)\)\s*as\b", re.IGNORECASE)
_ALTER_VIEW_RE = re.compile(
    rf"\balter\s+view\s+(?:if\s+exists\s+)?{_NAME}\s+set\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_INVOKER_RE = re.compile(r"\bsecurity_invoker\b(?:\s*=\s*'?(\w+)'?)?", re.IGNORECASE)
_STATEMENT_END_RE = re.compile(r";")


@dataclass
class _Object:
    file: str
    line: int
    name: str
    secured: bool = False


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def strip_sql(text: str) -> str:
    """The SQL with comments, string literals and dollar-quoted bodies blanked
    (line breaks kept), so only statements are matched."""
    text = re.sub(r"\$(\w*)\$.*?\$\1\$", _blank, text, flags=re.DOTALL)
    text = re.sub(r"/\*.*?\*/", _blank, text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", _blank, text)
    return re.sub(r"'(?:[^']|'')*'", _blank, text)


def _key(raw: str) -> tuple[str, str]:
    """``"public"."todos"`` / ``todos`` → ``("public", "todos")``."""
    parts = [p.strip('"').lower() for p in re.findall(r'"[^"]+"|[^".\s]+', raw)]
    return (
        (parts[0], parts[1])
        if len(parts) >= 2
        else ("public", parts[0] if parts else "")
    )


def _invoker_on(options: str) -> bool | None:
    """The ``security_invoker`` option's value in a WITH/SET list, if present."""
    match = _INVOKER_RE.search(options)
    if match is None:
        return None
    return (match.group(1) or "true").lower() not in {"false", "off", "0", "no"}


def _sql_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in _SQL_DIRS:
        base = root / directory
        if base.is_dir():
            files.extend(sorted(base.rglob("*.sql")))
    return files


def _events(text: str) -> list[tuple[int, str, re.Match]]:
    """Every relevant statement in *text* as (offset, kind, match), in order."""
    found: list[tuple[int, str, re.Match]] = []
    for kind, pattern in (
        ("table", _CREATE_TABLE_RE),
        ("enable", _ENABLE_RLS_RE),
        ("disable", _DISABLE_RLS_RE),
        ("rename", _RENAME_TABLE_RE),
        ("drop", _DROP_RE),
        ("view", _CREATE_VIEW_RE),
        ("alter_view", _ALTER_VIEW_RE),
    ):
        found.extend((m.start(), kind, m) for m in pattern.finditer(text))
    return sorted(found, key=lambda item: item[0])


def _statement(text: str, start: int) -> str:
    end = _STATEMENT_END_RE.search(text, start)
    return text[start : end.start() if end else len(text)]


def _replay(
    root: Path,
) -> tuple[dict[tuple[str, str], _Object], dict[tuple[str, str], _Object], int]:
    """Apply the migrations: public tables (secured = RLS on) and views
    (secured = security_invoker), and the number of SQL files read."""
    tables: dict[tuple[str, str], _Object] = {}
    views: dict[tuple[str, str], _Object] = {}
    files = _sql_files(root)
    for path in files:
        try:
            text = strip_sql(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        filename = rel(str(path))
        for offset, kind, match in _events(text):
            line = text.count("\n", 0, offset) + 1
            if kind == "table":
                if _TEMP_RE.search(match.group(0)):
                    continue
                key = _key(match.group(1))
                tables[key] = _Object(filename, line, ".".join(key))
            elif kind in ("enable", "disable"):
                obj = tables.get(_key(match.group(1)))
                if obj is not None:
                    obj.secured = kind == "enable"
            elif kind == "rename":
                old = _key(match.group(1))
                obj = tables.pop(old, None)
                if obj is not None:
                    new = (old[0], match.group(2).strip('"').lower())
                    obj.name = ".".join(new)
                    tables[new] = obj
            elif kind == "drop":
                registry = tables if match.group(1).lower() == "table" else views
                for raw in re.split(r"\s*,\s*", match.group(2)):
                    registry.pop(_key(raw), None)
            elif kind == "view":
                if _TEMP_RE.search(match.group(0)):
                    continue
                key = _key(match.group(1))
                options = _VIEW_OPTIONS_RE.search(_statement(text, match.end()))
                invoker = bool(options and _invoker_on(options.group(1)))
                views[key] = _Object(filename, line, ".".join(key), invoker)
            elif kind == "alter_view":
                obj = views.get(_key(match.group(1)))
                value = _invoker_on(match.group(2)) if obj is not None else None
                if obj is not None and value is not None:
                    obj.secured = value
    return tables, views, len(files)


def scan_rls_disabled_in_public(root: Path) -> tuple[list[dict], int]:
    """Public tables the migrations never enable row-level security on."""
    tables, _views, scanned = _replay(root)
    entries = [
        {"file": obj.file, "line": obj.line, "table": obj.name}
        for key, obj in sorted(tables.items())
        if key[0] == "public" and not obj.secured
    ]
    return entries, scanned


def scan_security_definer_views(root: Path) -> tuple[list[dict], int]:
    """Public views without ``security_invoker``: they run as their owner, so
    they skip the RLS of the tables they read."""
    _tables, views, scanned = _replay(root)
    entries = [
        {"file": obj.file, "line": obj.line, "view": obj.name}
        for key, obj in sorted(views.items())
        if key[0] == "public" and not obj.secured
    ]
    return entries, scanned


def edge_function_entries(package_root: Path) -> frozenset[str]:
    """``supabase/functions/<name>/index.ts``: each Edge Function's entry."""
    base = package_root / FUNCTIONS_DIR
    if not base.is_dir():
        return frozenset()
    found = set()
    for child in base.iterdir():
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        for suffix in (".ts", ".js", ".tsx", ".jsx", ".mts", ".mjs"):
            if (child / f"index{suffix}").is_file():
                found.add(f"{FUNCTIONS_DIR}/{child.name}/index{suffix}")
    return frozenset(found)


def is_edge_function_entry(filepath: str) -> bool:
    """Whether *filepath* is an Edge Function's ``index`` module."""
    parts = filepath.replace("\\", "/").split("/")
    return (
        len(parts) >= 4
        and parts[-4:-2] == [SUPABASE_DIR, "functions"]
        and not parts[-2].startswith("_")
        and parts[-1].rsplit(".", 1)[0] == "index"
    )


__all__ = [
    "FUNCTIONS_DIR",
    "edge_function_entries",
    "is_edge_function_entry",
    "scan_rls_disabled_in_public",
    "scan_security_definer_views",
    "strip_sql",
]
