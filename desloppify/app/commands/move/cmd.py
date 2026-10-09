"""move command: move a file or directory and update all import references."""

from __future__ import annotations

import argparse
from pathlib import Path

from desloppify.app.commands.move.apply import apply_file_move, check_rewrite_syntax
from desloppify.app.commands.move.directory import run_directory_move
from desloppify.app.commands.move.language import (
    load_move_module,
    resolve_move_verify_hint,
)
from desloppify.app.commands.move.planning import (
    check_unrewritable_importers,
    compute_replacements,
    move_graph_root,
    resolve_dest,
)
from desloppify.app.commands.move.reporting import print_file_move_plan
from desloppify.base.discovery.file_paths import (
    rel,
    resolve_path,
)
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.languages import framework as lang_mod


def cmd_move(args: argparse.Namespace) -> None:
    """Move a file or directory and update all import references."""
    source_rel = args.source
    source_abs = resolve_path(source_rel)
    source_path = Path(source_abs)

    if source_path.is_dir():
        return _cmd_move_dir(args, source_abs)

    if not source_path.is_file():
        raise CommandError(f"Source not found: {rel(source_abs)}")

    dest_abs = resolve_dest(source_rel, args.dest, resolve_path)
    if Path(dest_abs).exists():
        raise CommandError(f"Destination already exists: {rel(dest_abs)}")

    dry_run = getattr(args, "dry_run", False)

    lang = lang_mod.default_lang()
    move_mod = load_move_module()

    scan_path = move_graph_root(
        move_mod, Path(resolve_path(lang.default_src)), get_project_root()
    )
    graph = lang.build_dep_graph(scan_path)
    importer_changes, self_changes = compute_replacements(
        move_mod,
        source_abs,
        dest_abs,
        graph,
    )

    print_file_move_plan(source_abs, dest_abs, importer_changes, self_changes)
    check_unrewritable_importers(
        move_mod,
        graph,
        {source_abs},
        set(importer_changes),
        dry_run=dry_run,
        force=getattr(args, "force", False),
        rel_fn=rel,
        warn_fn=lambda msg: print(colorize(f"  ⚠ {msg}", "yellow")),
    )
    check_rewrite_syntax(
        {**importer_changes, source_abs: self_changes}, dry_run=dry_run
    )
    if dry_run:
        print(colorize("  Dry run — no files modified.", "yellow"))
        return

    apply_file_move(source_abs, dest_abs, importer_changes, self_changes)

    print(colorize("  Done.", "green"))
    verify_hint = resolve_move_verify_hint(move_mod)
    if verify_hint:
        print(colorize(f"  Run `{verify_hint}` to verify.", "dim"))
    print()


def _cmd_move_dir(args, source_abs: str):
    """Move a directory (package) and update all import references."""
    run_directory_move(args, source_abs, resolve_path)
