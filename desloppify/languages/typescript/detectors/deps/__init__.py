"""Dependency graph + coupling analysis (fan-in/fan-out) + dynamic imports."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import (
    find_component_files,
    find_source_files,
    find_ts_and_js_files,
)
from desloppify.base.output.terminal import colorize, print_table
from desloppify.base.search.grep import grep_files
from desloppify.engine.detectors.graph import (
    detect_cycles,
    finalize_graph,
    get_coupling_score,
)
from desloppify.languages.typescript.detectors.deps.imports import (
    DYNAMIC_PREFIX,
    GLOB,
    ImportExtractor,
    ImportRef,
    extract_imports_regex,
)
from desloppify.languages.typescript.detectors.deps.resolver import (
    ModuleResolver,
    docusaurus_site_root,
    is_bare,
)
from desloppify.languages.typescript.detectors.deps.resolve import (
    load_tsconfig_paths as _load_tsconfig_paths,
)
from desloppify.languages.typescript.detectors.deps.resolve import (
    specifier_target as _specifier_target,
)
from desloppify.languages.typescript.detectors.deps.runtime import (
    build_dynamic_import_targets as _build_dynamic_import_targets,
)
from desloppify.languages.typescript.detectors.deps.runtime import (
    ts_alias_resolver as _ts_alias_resolver,
)

_FRAMEWORK_EXTENSIONS = (".svelte", ".vue", ".astro")
# Documents whose ESM imports are edges: MDX docs import components, and
# Docusaurus compiles a site's Markdown as MDX too.
_DOCUMENT_EXTENSIONS = (".mdx",)
_DOCUSAURUS_MARKDOWN = (".md",)
_DENO_EXTERNAL_PREFIXES = ("http://", "https://", "npm:", "jsr:")
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")


def _extract_module_specifiers(line: str) -> list[str]:
    """Extract static import/export module specifiers from one source line."""
    return [ref.specifier for ref in extract_imports_regex(line)]


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a bundler glob (``**``, ``*``, ``?``, ``{a,b}``) to a regex."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "{":
            close = pattern.find("}", i)
            if close == -1:
                out.append(re.escape(ch))
            else:
                options = pattern[i + 1 : close].split(",")
                out.append("(?:" + "|".join(re.escape(o) for o in options) + ")")
                i = close + 1
                continue
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _pattern_targets(
    ref: ImportRef,
    filepath: str,
    tsconfig_paths: dict[str, str],
    tsconfig_root: Path,
    project_root: Path,
    candidates: list[str],
) -> list[str]:
    """Files matched by ``import.meta.glob('./x/*.ts')`` or ``import(`./x/${y}`)``."""
    specifier = ref.specifier
    if ref.kind == GLOB:
        cut = min((specifier.find(c) for c in "*?{" if c in specifier), default=len(specifier))
        static_head = specifier[: specifier.rfind("/", 0, cut) + 1]
    else:
        static_head = specifier[: specifier.rfind("/") + 1]
    tail = specifier[len(static_head) :]
    if ref.kind == GLOB and static_head.startswith("/"):
        base: Path | None = (project_root / static_head.lstrip("/")).resolve()  # Vite: root-relative
    else:
        base = _specifier_target(
            static_head or "./", filepath, tsconfig_paths, tsconfig_root, source_root=project_root
        )
    if base is None:
        return []
    base_str = str(base).rstrip(os.sep) + os.sep
    if ref.kind == DYNAMIC_PREFIX:
        return [c for c in candidates if c.startswith(base_str + tail)]
    tail_re = _glob_regex(tail)
    return [
        c
        for c in candidates
        if c.startswith(base_str) and tail_re.match(c[len(base_str) :].replace(os.sep, "/"))
    ]


def build_dep_graph(
    path: Path,
    roslyn_cmd: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a dependency graph: for each file, who it imports and who imports it.

    Returns {resolved_path: {"imports": set[str], "importers": set[str],
    "deferred_imports": set[str], "import_count": int, "importer_count": int}}.
    Nodes with bare specifiers that resolve to nothing (and name no declared
    dependency) also get ``unresolved_imports``.
    ``deferred_imports`` holds targets reached only through type-only, dynamic,
    mock or triple-slash references: real dependencies, but not ones that run
    at module initialization, so they can't form an import cycle.
    """
    del roslyn_cmd
    graph: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"imports": set(), "importers": set(), "external_imports": set()}
    )
    project_root = get_project_root()
    resolver = ModuleResolver(path, project_root)
    extractor = ImportExtractor()

    # Components (.vue, .svelte, .astro) are modules too: their script
    # blocks import, and other files import them.
    ts_files = [*find_ts_and_js_files(path), *find_component_files(path)]
    # Seed every module so files with no imports of their own (constants,
    # types, leaf utilities) can still be found orphaned. Ambient
    # declaration files are never imported, so they stay out of the graph.
    seeded: list[str] = []
    for filepath in ts_files:
        if not filepath.endswith(_DECLARATION_SUFFIXES):
            resolved = resolve_path(filepath)
            graph[resolved]
            seeded.append(resolved)

    runtime_edges: dict[str, set[str]] = defaultdict(set)
    pattern_refs: list[tuple[str, str, ImportRef]] = []
    fw_files = find_source_files(path, list(_DOCUMENT_EXTENSIONS))
    fw_files += [
        f
        for f in find_source_files(path, list(_DOCUSAURUS_MARKDOWN))
        if docusaurus_site_root(resolve_path(f)) is not None
    ]
    for filepath in [*ts_files, *fw_files]:
        source_resolved = resolve_path(filepath)
        refs = extractor.extract(source_resolved)
        if not refs:
            continue
        graph[source_resolved]  # ensure entry exists
        for ref in refs:
            module_path = ref.specifier
            if module_path.startswith(_DENO_EXTERNAL_PREFIXES):
                graph[source_resolved]["external_imports"].add(module_path)
                continue
            if ref.kind in (GLOB, DYNAMIC_PREFIX):
                pattern_refs.append((filepath, source_resolved, ref))
                continue
            target = resolver.resolve(module_path, source_resolved)
            if target is not None:
                graph[source_resolved]["imports"].add(target)
                graph[target]["importers"].add(source_resolved)
            elif is_bare(module_path) and not resolver.is_external(module_path, source_resolved):
                # Not a dependency and not resolved: probably an alias the
                # resolver doesn't understand, so the file it names may look
                # orphaned when it isn't.
                graph[source_resolved].setdefault("unresolved_imports", set()).add(module_path)
            if target is not None and ref.runtime:
                runtime_edges[source_resolved].add(target)

    for filepath, source_resolved, ref in pattern_refs:
        tsconfig_root, tsconfig_paths = resolver.tsconfigs.for_file(source_resolved)
        for target in _pattern_targets(
            ref, filepath, tsconfig_paths, tsconfig_root, project_root, seeded
        ):
            if target != source_resolved:
                graph[source_resolved]["imports"].add(target)
                graph[target]["importers"].add(source_resolved)

    for source, node in graph.items():
        node["deferred_imports"] = node["imports"] - runtime_edges.get(source, set())

    return finalize_graph(dict(graph))


def cmd_deps(args: Any) -> None:
    """Show dependency info for a specific file or top coupled files."""
    graph = build_dep_graph(Path(args.path))

    if hasattr(args, "file") and args.file:
        # Single file mode
        coupling = get_coupling_score(args.file, graph)
        if args.json:
            print(json.dumps({"file": rel(args.file), **coupling}, indent=2))
            return
        print(colorize(f"\nDependency info: {rel(args.file)}\n", "bold"))
        print(f"  Fan-in (importers):  {coupling['fan_in']}")
        print(f"  Fan-out (imports):   {coupling['fan_out']}")
        print(f"  Instability:         {coupling['instability']}")
        if coupling["importers"]:
            print(colorize(f"\n  Imported by ({coupling['fan_in']}):", "cyan"))
            for p in coupling["importers"][:20]:
                print(f"    {p}")
        if coupling["imports"]:
            print(colorize(f"\n  Imports ({coupling['fan_out']}):", "cyan"))
            for p in coupling["imports"][:20]:
                print(f"    {p}")
        return

    # Top coupled files mode
    scored = []
    for filepath, entry in graph.items():
        total = entry["import_count"] + entry["importer_count"]
        if total > 5:
            scored.append(
                {
                    "file": filepath,
                    "fan_in": entry["importer_count"],
                    "fan_out": entry["import_count"],
                    "total": total,
                }
            )
    scored.sort(key=lambda x: -x["total"])

    if args.json:
        print(
            json.dumps(
                {
                    "count": len(scored),
                    "entries": [
                        {**s, "file": rel(s["file"])} for s in scored[: args.top]
                    ],
                },
                indent=2,
            )
        )
        return

    print(colorize(f"\nMost coupled files: {len(scored)} with >5 connections\n", "bold"))
    rows = []
    for s in scored[: args.top]:
        rows.append(
            [rel(s["file"]), str(s["fan_in"]), str(s["fan_out"]), str(s["total"])]
        )
    print_table(["File", "In", "Out", "Total"], rows, [60, 5, 5, 6])


def cmd_cycles(args: Any) -> None:
    """Show import cycles in the codebase."""
    graph = build_dep_graph(Path(args.path))
    cycles, _ = detect_cycles(graph)

    if args.json:
        print(
            json.dumps(
                {
                    "count": len(cycles),
                    "cycles": [
                        {"length": cy["length"], "files": [rel(f) for f in cy["files"]]}
                        for cy in cycles
                    ],
                },
                indent=2,
            )
        )
        return

    if not cycles:
        print(colorize("\nNo import cycles found.", "green"))
        return

    print(colorize(f"\nImport cycles: {len(cycles)}\n", "bold"))
    for i, cy in enumerate(cycles[: args.top]):
        files = [rel(f) for f in cy["files"]]
        print(
            colorize(
                f"  Cycle {i + 1} ({cy['length']} files):",
                "red" if cy["length"] > 3 else "yellow",
            )
        )
        for f in files[:8]:
            print(f"    {f}")
        if len(files) > 8:
            print(f"    ... +{len(files) - 8} more")
        print()


def build_dynamic_import_targets(path: Path, extensions: list[str]) -> set[str]:
    """Find files referenced by dynamic imports (import('...')) and side-effect imports."""
    return _build_dynamic_import_targets(
        path,
        extensions,
        framework_extensions=_FRAMEWORK_EXTENSIONS,
        grep_files_fn=grep_files,
        find_source_files_fn=find_source_files,
    )


def ts_alias_resolver(target: str) -> str:
    """Resolve TS path aliases using tsconfig.json paths."""
    return _ts_alias_resolver(
        target,
        load_paths_fn=_load_tsconfig_paths,
        project_root=get_project_root(),
    )
