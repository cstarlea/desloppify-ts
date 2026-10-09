"""Cross-language security detector entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_scan_file
from desloppify.base.discovery.sfc import read_code_text
from desloppify.engine.hook_registry import get_lang_hook
from desloppify.engine.policy.zones import FileZoneMap

from .filters import _is_test_file, _should_scan_file, _should_skip_line
from .scanner import _scan_line_for_security_entries

logger = logging.getLogger(__name__)


def detect_security_issues(
    files: list[str],
    zone_map: FileZoneMap | None,
    lang_name: str,
    *,
    scan_root: Path | None = None,
) -> tuple[list[dict], int]:
    """Detect cross-language security issues in source files.

    A language with a ``security`` hook gives each file's lines with comments
    and literals blanked, so each rule picks what it matches in (see the
    scanner); otherwise lines that start as comments are skipped.
    """
    line_views = getattr(get_lang_hook(lang_name, "security"), "line_views", None)
    entries: list[dict] = []
    scanned = 0

    resolved_scan_root = scan_root.resolve() if isinstance(scan_root, Path) else Path.cwd()

    for filepath in files:
        if not _should_scan_file(filepath, zone_map):
            continue

        try:
            resolved_path = resolve_scan_file(filepath, scan_root=resolved_scan_root)
            content = read_code_text(resolved_path, errors="replace")
        except OSError as exc:
            logger.debug(
                "Skipping unreadable file in security detector: %s (%s)", filepath, exc
            )
            continue

        scanned += 1
        is_test = _is_test_file(filepath, zone_map)

        if line_views is not None:
            lines, code_lines, uncommented_lines = line_views(content, resolved_path)
            for index, line in enumerate(lines):
                entries.extend(
                    _scan_line_for_security_entries(
                        filepath=filepath,
                        line_num=index + 1,
                        line=line,
                        is_test=is_test,
                        code=code_lines[index],
                        uncommented=uncommented_lines[index],
                    )
                )
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            if _should_skip_line(line):
                continue
            entries.extend(
                _scan_line_for_security_entries(
                    filepath=filepath,
                    line_num=line_num,
                    line=line,
                    is_test=is_test,
                )
            )

    return entries, scanned
