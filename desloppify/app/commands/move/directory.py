"""Directory-move orchestration for the move command."""

from __future__ import annotations

from pathlib import Path

from desloppify.app.commands.move.apply import (
    apply_directory_move,
    check_rewrite_syntax,
)
from desloppify.app.commands.move.language import (
    load_move_module,
    resolve_move_verify_hint,
)
from desloppify.app.commands.move.planning import (
    build_directory_move_plan,
    build_internal_directory_changes,
    check_unrewritable_importers,
    collect_source_files,
    move_graph_root,
)
from desloppify.app.commands.move.reporting import print_directory_move_plan
from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.languages import framework as lang_mod


def run_directory_move(args, source_abs: str, resolve_path_fn) -> None:
    """Move a directory and update all import references."""
    source_path = Path(source_abs)
    dest_abs = resolve_path_fn(args.dest)
    dry_run = getattr(args, "dry_run", False)

    if Path(dest_abs).exists():
        raise CommandError(f"Destination already exists: {rel(dest_abs)}")

    lang = lang_mod.default_lang()
    lang_name = lang.name
    move_mod = load_move_module()

    source_files = collect_source_files(source_path, list(lang.extensions))
    if not source_files:
        raise CommandError(f"No {lang_name} files found in {rel(source_abs)}")

    scan_path = move_graph_root(
        move_mod, Path(resolve_path_fn(lang.default_src)), get_project_root()
    )
    graph = lang.build_dep_graph(scan_path)
    plan = build_directory_move_plan(
        source_abs=source_abs,
        source_path=source_path,
        dest_abs=dest_abs,
        source_files=source_files,
        move_mod=move_mod,
        graph=graph,
    )

    print_directory_move_plan(source_abs, dest_abs, plan)
    check_unrewritable_importers(
        move_mod,
        graph,
        set(source_files),
        set(plan.external_changes),
        dry_run=dry_run,
        force=getattr(args, "force", False),
        rel_fn=rel,
        warn_fn=lambda msg: print(colorize(f"  ⚠ {msg}", "yellow")),
    )
    internal_changes = build_internal_directory_changes(plan)
    check_rewrite_syntax({**internal_changes, **plan.external_changes}, dry_run=dry_run)
    if dry_run:
        print(colorize("  Dry run — no files modified.", "yellow"))
        return

    apply_directory_move(
        source_abs=source_abs,
        dest_abs=dest_abs,
        source_path=source_path,
        external_changes=plan.external_changes,
        internal_changes=internal_changes,
    )

    print(colorize("  Done.", "green"))
    verify_hint = resolve_move_verify_hint(move_mod)
    if verify_hint:
        print(colorize(f"  Run `{verify_hint}` to verify.", "dim"))
    print()


__all__ = ["run_directory_move"]
