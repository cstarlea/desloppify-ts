"""Canonical TypeScript detector CLI and command-registry surface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.base.output.terminal import colorize, display_entries, plural, print_table
from desloppify.engine.detectors import coupling as coupling_detector_mod
from desloppify.engine.detectors import dupes as dupes_detector_mod
from desloppify.engine.detectors import gods as gods_detector_mod
from desloppify.engine.policy.zones import FileZoneMap
from desloppify.languages._framework.commands.base import (
    make_cmd_complexity,
    make_cmd_facade,
    make_cmd_large,
    make_cmd_naming,
    make_cmd_passthrough,
    make_cmd_single_use,
    make_cmd_smells,
)
from desloppify.languages._framework.commands.registry import (
    build_standard_detect_registry,
    compose_detect_registry,
)
from desloppify.languages.typescript.detectors.deps import (
    build_dep_graph,
)
from desloppify.languages.typescript.detectors.facade import detect_reexport_facades
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.detectors.concerns import cmd_concerns
from desloppify.languages.typescript.detectors.deprecated import cmd_deprecated
from desloppify.languages.typescript.detectors.deps import cmd_cycles, cmd_deps
from desloppify.languages.typescript.detectors.exports import cmd_exports
from desloppify.languages.typescript.detectors.logs import cmd_logs
from desloppify.languages.typescript.detectors.patterns.cli import cmd_patterns
from desloppify.languages.typescript.detectors.props import cmd_props
from desloppify.languages.typescript.detectors.react.cli import cmd_react
from desloppify.languages.typescript.detectors.unused import cmd_unused
from desloppify.languages.typescript.extractors_classes import extract_ts_classes
from desloppify.languages.typescript.extractors_components import (
    detect_passthrough_components,
    extract_ts_components,
)
from desloppify.languages.typescript.extractors_functions import extract_ts_functions
from desloppify.languages.typescript.phases_coupling import find_orphans, package_context
from desloppify.languages.typescript.phases_config import (
    TS_CLASS_GOD_RULES,
    TS_COMPLEXITY_SIGNALS,
    TS_GOD_RULES,
    TS_SKIP_NAMES,
)
from desloppify.languages.typescript.presets import resolve_layers, shadcn_ui_dirs
from desloppify.languages.typescript.plugin_contract import (
    TS_BARREL_NAMES,
    TS_LARGE_THRESHOLD,
)


cmd_large = make_cmd_large(
    find_ts_and_js_files,
    default_threshold=TS_LARGE_THRESHOLD,
    module_name=__name__,
)
cmd_complexity = make_cmd_complexity(
    find_ts_and_js_files,
    TS_COMPLEXITY_SIGNALS,
    module_name=__name__,
)
cmd_single_use = make_cmd_single_use(
    build_dep_graph,
    barrel_names=TS_BARREL_NAMES,
    module_name=__name__,
)
cmd_passthrough = make_cmd_passthrough(
    detect_passthrough_components,
    noun="component",
    name_key="component",
    total_key="total_props",
    module_name=__name__,
)
cmd_naming = make_cmd_naming(
    find_ts_and_js_files,
    skip_names=TS_SKIP_NAMES,
    skip_dirs=lambda: set(shadcn_ui_dirs()),
    module_name=__name__,
)
cmd_smells = make_cmd_smells(
    detect_smells,
    module_name=__name__,
)
cmd_facade = make_cmd_facade(
    build_dep_graph,
    detect_facades_fn=detect_reexport_facades,
    module_name=__name__,
)


def cmd_gods(args: argparse.Namespace) -> None:
    path = Path(args.path)
    components, _ = gods_detector_mod.detect_gods(extract_ts_components(path), TS_GOD_RULES)
    god_classes, _ = gods_detector_mod.detect_gods(extract_ts_classes(path), TS_CLASS_GOD_RULES)
    entries = sorted(components + god_classes, key=lambda e: -e["loc"])
    display_entries(
        args,
        entries,
        label="God components and classes",
        empty_msg="No god components or classes found.",
        columns=["File", "Name", "LOC", "Why"],
        widths=[50, 20, 5, 45],
        row_fn=lambda e: [
            rel(e["file"]),
            str(e.get("name", "")),
            str(e["loc"]),
            ", ".join(e["reasons"]),
        ],
    )


def cmd_orphaned(args: argparse.Namespace) -> None:
    """Orphaned files exactly as ``scan`` reports them: same zones, entries, confidence."""
    from desloppify.languages.framework import get_lang, make_lang_run

    path = Path(args.path)
    lang = make_lang_run(get_lang("typescript"))
    config = getattr(getattr(args, "runtime", None), "config", None) or {}
    lang.zone_map = FileZoneMap(
        lang.file_finder(path),
        lang.zone_rules,
        rel_fn=rel,
        overrides=config.get("zone_overrides") or None,
    )
    graph = build_dep_graph(path)
    packages, package_entries = package_context(path, graph)
    entries, _ = find_orphans(path, graph, lang, packages, package_entries)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "count": len(entries),
                    "entries": [
                        {
                            "file": rel(e["file"]),
                            "loc": e["loc"],
                            "confidence": e.get("confidence", "medium"),
                        }
                        for e in entries
                    ],
                },
                indent=2,
            )
        )
        return
    if not entries:
        print(colorize("\nNo orphaned files found.", "green"))
        return
    total_loc = sum(e["loc"] for e in entries)
    print(colorize(f"\nOrphaned files: {plural(len(entries), 'file')}, {total_loc} LOC\n", "bold"))
    top = getattr(args, "top", 20)
    rows = [[rel(e["file"]), str(e["loc"])] for e in entries[:top]]
    print_table(["File", "LOC"], rows, [80, 6])
    if len(entries) > top:
        print(f"\n  ... and {len(entries) - top} more")


def cmd_dupes(args: argparse.Namespace) -> None:
    functions = []
    for filepath in find_ts_and_js_files(Path(args.path)):
        if "node_modules" in filepath or ".d.ts" in filepath:
            continue
        functions.extend(extract_ts_functions(filepath))
    entries, _ = dupes_detector_mod.detect_duplicates(
        functions, threshold=getattr(args, "threshold", None) or 0.8
    )
    if getattr(args, "json", False):
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return
    if not entries:
        print(colorize("No duplicate functions found.", "green"))
        return
    exact = [e for e in entries if e["kind"] == "exact"]
    near = [e for e in entries if e["kind"] == "near-duplicate"]
    if exact:
        print(colorize(f"\nExact duplicates: {len(exact)} pairs\n", "bold"))
        rows = []
        for entry in exact[: getattr(args, "top", 20)]:
            fn_a, fn_b = entry["fn_a"], entry["fn_b"]
            rows.append(
                [
                    f"{fn_a['name']} ({rel(fn_a['file'])}:{fn_a['line']})",
                    f"{fn_b['name']} ({rel(fn_b['file'])}:{fn_b['line']})",
                    str(fn_a["loc"]),
                ]
            )
        print_table(["Function A", "Function B", "LOC"], rows, [50, 50, 5])
    if near:
        print(
            colorize(
                f"\nNear-duplicates (>={getattr(args, 'threshold', 0.8):.0%} similar): {len(near)} pairs\n",
                "bold",
            )
        )
        rows = []
        for entry in near[: getattr(args, "top", 20)]:
            fn_a, fn_b = entry["fn_a"], entry["fn_b"]
            rows.append(
                [
                    f"{fn_a['name']} ({rel(fn_a['file'])}:{fn_a['line']})",
                    f"{fn_b['name']} ({rel(fn_b['file'])}:{fn_b['line']})",
                    f"{entry['similarity']:.0%}",
                ]
            )
        print_table(["Function A", "Function B", "Sim"], rows, [50, 50, 5])


def cmd_coupling(args: argparse.Namespace) -> None:
    path = Path(args.path)
    graph = build_dep_graph(path)
    layers = resolve_layers(path, getattr(args, "lang_run", None))
    if not layers:
        print(
            colorize(
                "\nNo layers configured: set `presets` (feature-sliced, bulletproof-react) "
                "or languages.typescript.layers in .desloppify/config.json.",
                "yellow",
            )
        )
        return
    violations, _ = coupling_detector_mod.detect_layer_violations(path, graph, layers)
    candidates, _ = coupling_detector_mod.detect_boundary_candidates(
        path,
        graph,
        layers,
        skip_basenames=TS_BARREL_NAMES,
        skip_dirs=shadcn_ui_dirs(),
    )
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "layers": [layer.name for layer in layers],
                    "violations": len(violations),
                    "boundary_candidates": len(candidates),
                    "coupling_violations": [
                        {**entry, "file": rel(entry["file"])} for entry in violations
                    ],
                    "boundary_candidates_detail": [
                        {**entry, "file": rel(entry["file"])} for entry in candidates
                    ],
                },
                indent=2,
            )
        )
        return
    top = getattr(args, "top", 20)
    print(colorize(f"\nLayers (top first): {' > '.join(layer.name for layer in layers)}", "dim"))
    if violations:
        print(colorize(f"\nLayer violations: {len(violations)}\n", "bold"))
        rows = [
            [rel(entry["file"]), entry["target"], entry["direction"]]
            for entry in violations[:top]
        ]
        print_table(["File", "Imports", "Direction"], rows, [50, 50, 25])
    else:
        print(colorize("\nNo layer violations.", "green"))

    print()
    if candidates:
        print(
            colorize(
                f"Boundary candidates (shared files used by one slice): {len(candidates)}\n",
                "bold",
            )
        )
        rows = [
            [
                rel(entry["file"]),
                str(entry["loc"]),
                entry["sole_slice"],
                str(entry["importer_count"]),
            ]
            for entry in candidates[:top]
        ]
        print_table(
            ["Shared File", "LOC", "Only Used By", "Importers"],
            rows,
            [50, 5, 30, 9],
        )
    else:
        print(colorize("No boundary candidates found.", "green"))
    print()


def get_detect_commands() -> dict[str, Callable[..., None]]:
    """Build the TypeScript detector command registry."""
    return compose_detect_registry(
        base_registry=build_standard_detect_registry(
            cmd_deps=cmd_deps,
            cmd_cycles=cmd_cycles,
            cmd_orphaned=cmd_orphaned,
            cmd_dupes=cmd_dupes,
            cmd_large=cmd_large,
            cmd_complexity=cmd_complexity,
        ),
        extra_registry={
            "logs": cmd_logs,
            "unused": cmd_unused,
            "exports": cmd_exports,
            "deprecated": cmd_deprecated,
            "gods": cmd_gods,
            "single_use": cmd_single_use,
            "props": cmd_props,
            "passthrough": cmd_passthrough,
            "concerns": cmd_concerns,
            "smells": cmd_smells,
            "coupling": cmd_coupling,
            "patterns": cmd_patterns,
            "naming": cmd_naming,
            "react": cmd_react,
            "facade": cmd_facade,
        },
    )


__all__ = [
    "cmd_complexity",
    "cmd_concerns",
    "cmd_coupling",
    "cmd_cycles",
    "cmd_deprecated",
    "cmd_deps",
    "cmd_dupes",
    "cmd_exports",
    "cmd_facade",
    "cmd_gods",
    "cmd_large",
    "cmd_logs",
    "cmd_naming",
    "cmd_orphaned",
    "cmd_passthrough",
    "cmd_patterns",
    "cmd_props",
    "cmd_react",
    "cmd_single_use",
    "cmd_smells",
    "cmd_unused",
    "get_detect_commands",
]
