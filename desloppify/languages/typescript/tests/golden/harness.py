"""Run a full mechanical TypeScript scan over a golden fixture project.

The scan is hermetic: the project is copied to a temp directory (so
path-based zone rules see only the project's own layout) and ``PATH`` is
reduced to ``git`` so no Node toolchain (tsc, npx, eslint, knip) can leak in
from the developer's machine. ``node_tools=True`` is the opt-in layer: it
links the pinned tsc/knip from ``golden/node/node_modules`` into the project
and adds only the ``node`` binary to ``PATH`` (still no npx or global tools).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.engine.planning.scan import _generate_issues_from_lang
from desloppify.languages.framework import (
    disable_parse_cache,
    enable_parse_cache,
    get_lang,
    make_lang_run,
    record_grammar_load_failures,
    reset_grammar_load_failures,
    reset_script_import_caches,
)
from desloppify.languages.typescript.detectors.deps.resolve import (
    load_tsconfig_paths_cached,
)
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache

GOLDEN_DIR = Path(__file__).parent
PROJECTS_DIR = GOLDEN_DIR / "projects"
SNAPSHOTS_DIR = GOLDEN_DIR / "snapshots"
NODE_MODULES = GOLDEN_DIR / "node" / "node_modules"
UPDATE_ENV = "DESLOPPIFY_UPDATE_GOLDEN"


def project_names() -> list[str]:
    return sorted(p.name for p in PROJECTS_DIR.iterdir() if p.is_dir())


def node_tools_installed() -> bool:
    return (NODE_MODULES / ".bin" / "tsc").is_file() and (NODE_MODULES / ".bin" / "knip").is_file()


def _hermetic_path(bin_dir: Path, tools: tuple[str, ...]) -> str:
    """A PATH containing only ``tools``, so detectors can't find anything else."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        link = bin_dir / tool
        if found and not link.exists():
            link.symlink_to(found)
    return str(bin_dir)


def copy_project(name: str, dest_root: Path, *, node_tools: bool = False) -> Path:
    dest = dest_root / name
    shutil.copytree(PROJECTS_DIR / name, dest, ignore=shutil.ignore_patterns("node_modules"))
    if node_tools:
        (dest / "node_modules").symlink_to(NODE_MODULES)
    return dest


def scan_project(
    project: Path, *, node_tools: bool = False, cwd: Path | None = None
) -> dict[str, Any]:
    """Scan ``project`` and return a normalized, order-independent snapshot.

    ``cwd`` defaults to the project itself, as when the CLI runs inside it.
    """
    saved_path = os.environ.get("PATH", "")
    saved_cwd = os.getcwd()
    # Scans must not depend on the cwd (test_scan_does_not_depend_on_cwd
    # checks this); default to the project, as when the CLI runs inside it.
    os.chdir(cwd or project)
    tools = ("git", "node") if node_tools else ("git",)
    os.environ["PATH"] = _hermetic_path(project.parent / f".golden-bin-{len(tools)}", tools)
    load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    reset_grammar_load_failures()
    try:
        with runtime_scope(RuntimeContext(project_root=project)):
            clear_source_file_cache_for_tests()
            reset_script_import_caches(str(project))
            enable_parse_cache()
            try:
                lang = make_lang_run(get_lang("typescript"))
                issues, potentials = _generate_issues_from_lang(
                    project, lang, include_slow=True, profile="objective"
                )
                record_grammar_load_failures(lang)
            finally:
                disable_parse_cache()
                clear_source_file_cache_for_tests()
    finally:
        os.environ["PATH"] = saved_path
        os.chdir(saved_cwd)
        load_tsconfig_paths_cached.cache_clear()
        clear_resolver_cache()

    return {
        "issues": sorted(
            (
                {
                    "id": issue["id"],
                    "tier": issue.get("tier"),
                    "confidence": issue.get("confidence"),
                    "zone": issue.get("zone"),
                    "summary": issue.get("summary"),
                }
                for issue in issues
            ),
            key=lambda item: item["id"],
        ),
        "potentials": dict(sorted(potentials.items())),
        "reduced_coverage": sorted(
            f"{detector}: {record.get('reason', '')}"
            for detector, record in lang.detector_coverage.items()
            if isinstance(record, dict) and record.get("status") == "reduced"
        ),
    }


def snapshot_path(name: str, *, node_tools: bool = False) -> Path:
    suffix = ".node.json" if node_tools else ".json"
    return SNAPSHOTS_DIR / f"{name}{suffix}"


def dump_snapshot(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
