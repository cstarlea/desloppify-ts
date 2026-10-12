"""Scripts loaded by URL rather than imported.

An HTML page loads ``<script src="/app.mjs">`` and an app registers its
service worker with ``navigator.serviceWorker.register('/sw.js')``. Nothing
imports either file, so each is an entry point. A relative URL resolves
against the referencing file's directory; an absolute one against that
directory or any parent up to the scan root, or a static asset directory
(``public/``) beneath one of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.discovery.source import (
    find_source_files,
    find_ts_and_js_files,
    read_file_text,
)
from desloppify.languages.typescript.detectors.deps.resolve import resolve_target

_SCRIPT_SRC_RE = re.compile(
    r"""<script\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
_SERVICE_WORKER_RE = re.compile(
    r"""serviceWorker\s*\.\s*register\(\s*(?:new\s+URL\(\s*)?['"`]([^'"`$]+)['"`]"""
)
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _resolve_url(
    url: str, base_dir: Path, root: Path, static_dirs: Iterable[str]
) -> str | None:
    url = url.split("#", 1)[0].split("?", 1)[0].strip()
    if not url or _EXTERNAL_RE.match(url):
        return None
    if not url.startswith("/"):
        return resolve_target(base_dir / url)
    relative = url.lstrip("/")
    directory = base_dir
    while True:
        for prefix in ("", *static_dirs):
            target = resolve_target(directory / prefix / relative)
            if target is not None:
                return target
        if directory == root or root not in directory.parents:
            return None
        directory = directory.parent


def url_entries(path: Path, static_dirs: Iterable[str] = ()) -> set[str]:
    """Absolute paths of scripts that HTML pages or service-worker registrations name."""
    root = path.resolve()
    static_dirs = tuple(static_dirs)
    entries: set[str] = set()
    for filepath in find_source_files(path, [".html"]):
        _collect(filepath, "", _SCRIPT_SRC_RE, root, static_dirs, entries)
    for filepath in find_ts_and_js_files(path):
        _collect(
            filepath, "serviceWorker", _SERVICE_WORKER_RE, root, static_dirs, entries
        )
    return entries


def _collect(
    filepath: str,
    needle: str,
    pattern: re.Pattern[str],
    root: Path,
    static_dirs: tuple[str, ...],
    entries: set[str],
) -> None:
    text = read_file_text(filepath)
    if not text or needle not in text:
        return
    base_dir = Path(resolve_path(filepath)).resolve().parent
    for url in pattern.findall(text):
        target = _resolve_url(url, base_dir, root, static_dirs)
        if target is not None:
            entries.add(target)


__all__ = ["url_entries"]
