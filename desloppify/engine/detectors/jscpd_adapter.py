"""Detect repeated code blocks via jscpd.

Replaces boilerplate_duplication.py with a thin adapter around jscpd
(https://github.com/kucherenko/jscpd), which uses proper per-language
tokenisation and supports type-2 clones (renamed identifiers).

Uses a jscpd installed in the project or on PATH. Downloading the pinned
version with npx happens only when the user allows it
(``allow_tool_download``); otherwise the phase is skipped with a hint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess  # nosec B404
import tempfile
from pathlib import Path

from desloppify.base.discovery.source import (
    collect_exclude_dirs,
    get_exclusions,
)
from desloppify.base.output.fallbacks import warn_best_effort
from desloppify.base.runtime_state import current_runtime_context

logger = logging.getLogger(__name__)

# Fetched by npx only when the user allows downloads; bump deliberately.
JSCPD_VERSION = "5.3.3"
DOWNLOAD_ENV = "DESLOPPIFY_ALLOW_TOOL_DOWNLOAD"

_BASE_IGNORES = (
    "**/node_modules/**",
    "**/.git/**",
    "**/__pycache__/**",
    "**/.venv*/**",
    "**/venv/**",
    "**/.desloppify/**",
    "**/.claude/**",
)

_ARTIFACT_PREFIXES = ("build/", "dist/", ".desloppify/", ".claude/")
_BUILD_MIRROR_PREFIX = "build/lib/"


def _to_scan_relative(path_resolved: Path, name: str) -> str | None:
    """Return scan-relative path, or None when file is outside scan path."""
    if not name:
        return None
    candidate = Path(name)
    if not candidate.is_absolute():
        # jscpd runs from the scan root and reports names relative to it.
        candidate = path_resolved / candidate
    if not candidate.exists() and ":" in candidate.name:
        # Blocks embedded in another format come back as ``file:format``.
        candidate = candidate.with_name(candidate.name.rsplit(":", 1)[0])
    try:
        rel = candidate.resolve().relative_to(path_resolved)
    except (ValueError, OSError):
        return None
    return str(rel).replace("\\", "/")


def _is_artifact_path(rel_path: str) -> bool:
    return rel_path.startswith(_ARTIFACT_PREFIXES)


def _is_build_mirror_pair(first_rel: str, second_rel: str) -> bool:
    if first_rel.startswith(_BUILD_MIRROR_PREFIX):
        return first_rel[len(_BUILD_MIRROR_PREFIX) :] == second_rel
    if second_rel.startswith(_BUILD_MIRROR_PREFIX):
        return second_rel[len(_BUILD_MIRROR_PREFIX) :] == first_rel
    return False


def _as_jscpd_globs(pattern: str) -> list[str]:
    raw = pattern.strip().replace("\\", "/").strip("/")
    if not raw:
        return []
    globs = [f"**/{raw}/**"]
    basename = raw.rsplit("/", 1)[-1]
    if "." in basename or "*" in basename:
        globs.append(f"**/{raw}")
    return globs


def _jscpd_ignore_arg(scan_path: Path) -> str:
    """Build jscpd ignore globs from defaults + runtime excludes."""
    patterns = set(_BASE_IGNORES)
    # Use collect_exclude_dirs for the directory-name portion, then convert
    # the remaining runtime globs (patterns with '*') to jscpd format.
    for dirname in collect_exclude_dirs(scan_path):
        base = Path(dirname).name
        patterns.update(_as_jscpd_globs(base))
    for pattern in get_exclusions():
        if "*" in pattern:
            patterns.update(_as_jscpd_globs(pattern))
    return ",".join(sorted(patterns))


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"(?<![:'\"])//[^\n]*")
_MODULE_SPEC = r"""(['"])[^'"\n]*\1"""
# Import and re-export statements, directives, and the cut-off import a
# window can start or end in.
_IMPORT_ONLY_PARTS = (
    re.compile(rf"\b(?:import|export)\b[\w\s,{{}}*$]*?\bfrom\s*{_MODULE_SPEC}\s*;?"),
    re.compile(rf"\bimport\s*{_MODULE_SPEC}\s*;?"),
    re.compile(r"""^\s*(['"])use (?:client|server|strict)\1\s*;?""", re.MULTILINE),
    re.compile(rf"^[\w\s,{{}}*$]*\}}\s*from\s*{_MODULE_SPEC}\s*;?"),
    re.compile(r"(?:\bimport\b|\bexport\s*(?:type\s*)?[{*])[\w\s,{}*$]*$"),
)


def _is_import_only(fragment: str) -> bool:
    """Whether a fragment holds nothing but imports, ``export ... from``,
    directives, comments and blank lines: every module repeats those."""
    text = _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", fragment))
    for pattern in _IMPORT_ONLY_PARTS:
        text = pattern.sub("", text)
    return not text.strip()


def _parse_jscpd_report(report: dict, scan_path: Path) -> list[dict]:
    """Parse a jscpd JSON report dict and return clustered duplication entries.

    Clusters pairwise duplicates that share the same fragment into multi-file
    groups, producing the same output shape as the old boilerplate_duplication
    detector so that phase_boilerplate_duplication() needs no further changes.
    """
    duplicates = report.get("duplicates", [])
    if not duplicates:
        return []

    path_resolved = scan_path.resolve()

    # Cluster pairs by SHA256(fragment.strip()[:200])[:16]
    clusters: dict[str, dict] = {}

    for dup in duplicates:
        fragment = dup.get("fragment", "")
        if _is_import_only(fragment):
            continue
        fragment_key = hashlib.sha256(
            fragment.strip()[:200].encode("utf-8", errors="replace")
        ).hexdigest()[:16]

        first = dup.get("firstFile", {})
        second = dup.get("secondFile", {})
        first_name = first.get("name", "")
        second_name = second.get("name", "")

        first_rel = _to_scan_relative(path_resolved, first_name)
        second_rel = _to_scan_relative(path_resolved, second_name)
        if first_rel is None or second_rel is None:
            continue
        if (
            first_rel == second_rel
            or _is_artifact_path(first_rel)
            or _is_artifact_path(second_rel)
            or _is_build_mirror_pair(first_rel, second_rel)
        ):
            continue

        lines = dup.get("lines", 0)
        if fragment_key not in clusters:
            clusters[fragment_key] = {
                "id": fragment_key,
                "lines": lines,
                "fragment": fragment,
                "files": {},
            }

        cluster = clusters[fragment_key]
        for name, info in [(first_rel, first), (second_rel, second)]:
            if name not in cluster["files"]:
                cluster["files"][name] = info.get("start", 0)

    entries: list[dict] = []
    for cluster in clusters.values():
        files = cluster["files"]
        if len(files) < 2:
            continue
        locations = [
            {"file": f, "line": line}
            for f, line in sorted(files.items(), key=lambda kv: kv[0])
        ]
        entries.append(
            {
                "id": cluster["id"],
                "distinct_files": len(files),
                "window_size": cluster["lines"],
                "locations": locations,
                "sample": cluster["fragment"].splitlines()[:4],
            }
        )

    entries.sort(key=lambda e: (-e["distinct_files"], e["id"]))
    return entries


def _find_local_jscpd(path: Path) -> Path | None:
    """``node_modules/.bin/jscpd`` at or above *path* (hoisted installs too)."""
    names = ("jscpd.cmd", "jscpd") if os.name == "nt" else ("jscpd",)
    start = path.resolve()
    for directory in (start, *start.parents):
        for name in names:
            candidate = directory / "node_modules" / ".bin" / name
            if candidate.is_file():
                return candidate
    return None


def download_allowed() -> bool:
    """Whether the user allowed fetching the pinned jscpd with npx."""
    if os.environ.get(DOWNLOAD_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return current_runtime_context().allow_tool_download


def _resolve_jscpd_command(path: Path) -> list[str] | str:
    """Return the command prefix for running jscpd, or a skip reason.

    A project-local jscpd wins over one on PATH. npx runs only the pinned
    version, and only when the user allowed downloads.
    """
    local = _find_local_jscpd(path)
    if local is not None:
        logger.debug("jscpd: using project install at %s", local)
        return [str(local)]
    jscpd_path = shutil.which("jscpd")
    if jscpd_path:
        logger.debug("jscpd: using installed executable at %s", jscpd_path)
        return [jscpd_path]
    if not download_allowed():
        return "jscpd_not_installed"
    npx_path = shutil.which("npx")
    if npx_path:
        logger.debug("jscpd: downloading jscpd@%s with npx", JSCPD_VERSION)
        return [npx_path, "--yes", f"jscpd@{JSCPD_VERSION}"]
    return "npx_not_found"


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Best-effort termination for jscpd and descendants on timeout."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        return


def _run_jscpd_command(
    cmd: list[str], *, timeout: int, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(  # nosec B603
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd, timeout, output=stdout, stderr=stderr
        ) from exc
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if proc.returncode:
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            output=stdout,
            stderr=stderr,
        )
    return result


def detect_with_jscpd(path: Path) -> list[dict] | None:
    """Run jscpd on *path* and return duplication entries, or None on failure."""
    return detect_with_jscpd_result(path)[0]


def detect_with_jscpd_result(path: Path) -> tuple[list[dict] | None, str | None]:
    """Return (entries, None), or (None, reason) when jscpd isn't available.

    Runtime failures warn and return (None, None).
    """
    cmd_prefix = _resolve_jscpd_command(path)
    if isinstance(cmd_prefix, str):
        logger.debug("jscpd: skipped (%s)", cmd_prefix)
        return None, cmd_prefix
    return _run_jscpd(path, cmd_prefix), None


def _run_jscpd(path: Path, cmd_prefix: list[str]) -> list[dict] | None:
    root = path.resolve()
    target = "."
    if root.is_file():
        root, target = root.parent, root.name
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            _run_jscpd_command(
                [
                    *cmd_prefix,
                    # jscpd matches --ignore globs against the paths it walks:
                    # from the scan root they are relative, so an ignore like
                    # **/.claude/** can't match a parent of the scan root.
                    target,
                    "--reporters",
                    "json",
                    "--output",
                    tmpdir,
                    "--min-lines",
                    "4",
                    "--min-tokens",
                    "50",
                    "--ignore",
                    _jscpd_ignore_arg(path),
                    "--silent",
                ],
                timeout=120,
                cwd=root,
            )
        except FileNotFoundError:
            warn_best_effort(
                "Boilerplate duplication detection skipped: jscpd not found."
            )
            logger.debug("jscpd: command not found at runtime — skipping")
            return None
        except subprocess.CalledProcessError as exc:
            warn_best_effort(
                "Boilerplate duplication detection skipped: jscpd exited with errors."
            )
            logger.debug(
                "jscpd: non-zero exit (%s): %s",
                exc.returncode,
                (exc.stderr or "").strip(),
            )
            return None
        except OSError as exc:
            warn_best_effort(
                "Boilerplate duplication detection skipped: jscpd failed to run."
            )
            logger.debug("jscpd: OS error running jscpd: %s", exc)
            return None
        except subprocess.TimeoutExpired:
            warn_best_effort(
                "Boilerplate duplication detection skipped: jscpd timed out after 120s."
            )
            logger.debug("jscpd: timed out")
            return None

        report_file = Path(tmpdir) / "jscpd-report.json"
        if not report_file.exists():
            logger.debug("jscpd: no report file produced — assuming no duplicates")
            return []

        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.debug("jscpd: failed to parse report: %s", exc)
            return None

        return _parse_jscpd_report(report, root)


__all__ = [
    "DOWNLOAD_ENV",
    "JSCPD_VERSION",
    "_parse_jscpd_report",
    "_resolve_jscpd_command",
    "_run_jscpd_command",
    "detect_with_jscpd",
    "detect_with_jscpd_result",
    "download_allowed",
]
