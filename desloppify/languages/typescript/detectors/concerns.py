"""Mixed concerns detection (UI + data fetching + transforms in one file)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel

from desloppify.base.discovery.source import find_tsx_and_jsx_files
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize, print_table
from desloppify.base.discovery.paths import get_project_root
from desloppify.languages._framework.frameworks.detection import framework_values
from desloppify.languages.typescript.syntax.scanner import file_code_text

logger = logging.getLogger(__name__)


def configured_data_clients(path: Path, lang: Any) -> tuple[str, ...]:
    """Data clients of the detected frameworks plus ``languages.typescript.data_clients``."""
    getter = getattr(lang, "runtime_setting", None)
    configured = getter("data_clients") if callable(getter) else None
    extra = tuple(str(n) for n in configured) if isinstance(configured, list) else ()
    return tuple(dict.fromkeys((*framework_values(path, lang, "data_clients"), *extra)))


_FETCH_RE = r"useQuery|useMutation|fetch\(|axios"


def detect_mixed_concerns(
    path: Path, data_clients: tuple[str, ...] = ()
) -> tuple[list[dict[str, Any]], int]:
    """Find files that mix UI rendering with data fetching, state management, and business logic.

    Heuristic: a .tsx file that has both JSX returns AND data fetching or
    direct calls on a data client (``data_clients``: ``supabase`` where the
    Supabase spec is detected, plus ``languages.typescript.data_clients``),
    or both UI components AND heavy data transformation.

    Returns (entries, total_files_checked).
    """
    clients = [name for name in data_clients if name]
    fetch_re = re.compile(_FETCH_RE + "".join(rf"|\b{re.escape(name)}\." for name in clients))
    direct_res = [
        (name, re.compile(rf"\b{re.escape(name)}\.\w+\.\w+\.\w+")) for name in clients
    ]
    files = find_tsx_and_jsx_files(path)
    entries = []
    for filepath in files:
        try:
            p = (
                Path(filepath)
                if Path(filepath).is_absolute()
                else get_project_root() / filepath
            )
            content = p.read_text(encoding="utf-8")
            loc = len(content.splitlines())
            if loc < 100:
                continue

            concerns = []
            # Patterns count in code only, not in comments or strings.
            content = file_code_text(content, p)

            # UI rendering
            has_jsx = bool(re.search(r"return\s*\(?\s*<", content))
            if has_jsx:
                concerns.append("jsx_rendering")

            # Data fetching
            if fetch_re.search(content):
                concerns.append("data_fetching")

            # Query chains on a data client (should be in hooks/services)
            concerns.extend(f"direct_{name}" for name, regex in direct_res if regex.search(content))

            # Heavy data transformation
            transform_patterns = len(
                re.findall(r"\.(map|filter|reduce|sort|flatMap)\s*\(", content)
            )
            if transform_patterns >= 3:
                concerns.append(f"data_transforms({transform_patterns})")

            # Event handler definitions (>5 = probably doing too much)
            handler_count = len(re.findall(r"(?:const|function)\s+handle\w+", content))
            if handler_count >= 5:
                concerns.append(f"handlers({handler_count})")

            # Flag if 3+ concern types in one file
            if len(concerns) >= 3:
                entries.append(
                    {
                        "file": filepath,
                        "loc": loc,
                        "concerns": concerns,
                        "concern_count": len(concerns),
                    }
                )
        except (OSError, UnicodeDecodeError) as exc:
            log_best_effort_failure(
                logger, f"read TSX concern candidate {filepath}", exc
            )
            continue
    return sorted(entries, key=lambda e: -e["concern_count"]), len(files)


def cmd_concerns(args: Any) -> None:
    path = Path(args.path)
    entries, _ = detect_mixed_concerns(
        path, configured_data_clients(path, getattr(args, "lang_run", None))
    )
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return
    if not entries:
        print(colorize("No mixed-concern files found.", "green"))
        return
    print(colorize(f"\nMixed concerns: {len(entries)} files\n", "bold"))
    rows = []
    for e in entries[: args.top]:
        rows.append([rel(e["file"]), str(e["loc"]), ", ".join(e["concerns"])])
    print_table(["File", "LOC", "Concerns"], rows, [55, 5, 50])
