"""zone command: show/set/clear zone classifications."""

from __future__ import annotations

import argparse
from pathlib import Path

from desloppify.app.commands.helpers.lang import resolve_lang
from desloppify.app.commands.helpers.rendering import print_agent_plan
from desloppify.app.commands.helpers.command_runtime import command_runtime
from desloppify.app.commands.helpers.state import state_path
from desloppify.base import config as config_mod
from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.engine.policy.zones import (
    FileZoneMap,
    Zone,
    is_override_pattern,
    matching_override,
)
from desloppify.state_io import load_state, save_state


def cmd_zone(args: argparse.Namespace) -> None:
    """Handle zone subcommands: show, set, clear."""
    action = getattr(args, "zone_action", None)
    if action in (None, "show"):
        _zone_show(args)
    elif action == "set":
        _zone_set(args)
    elif action == "clear":
        _zone_clear(args)
    else:
        raise CommandError("Usage: desloppify zone {show|set|clear}")


def _override_key(raw_path: str) -> str:
    """Normalize a file, directory, or glob argument to a ``zone_overrides`` key.

    A directory becomes ``dir/**``; a glob keeps its wildcard part and has its
    fixed leading directories made project-relative.
    """
    raw = raw_path.replace("\\", "/")
    if is_override_pattern(raw):
        segments = raw.split("/")
        fixed = next(i for i, seg in enumerate(segments) if is_override_pattern(seg))
        rest = "/".join(segments[fixed:])
        if fixed == 0:
            return rest
        prefix = rel("/".join(segments[:fixed]) or "/")
        return rest if prefix in ("", ".") else f"{prefix}/{rest}"
    normalized = rel(raw)
    if raw.endswith("/") or Path(resolve_path(raw)).is_dir():
        if normalized in ("", "."):
            return "**"
        return normalized.replace("[", "[[]") + "/**"
    return normalized


def _restamp_issues(args: argparse.Namespace, zone_for_file) -> int | None:
    """Set the stored zone of issues whose file *zone_for_file* returns a zone for."""
    try:
        sp = state_path(args)
        if not sp.exists():
            return 0
        state = load_state(sp)
        issues = state.get("work_items") or state.get("issues", {})
        updated = 0
        for issue in issues.values():
            file = issue.get("file")
            zone = zone_for_file(file) if isinstance(file, str) else None
            if zone is not None:
                issue["zone"] = zone
                updated += 1
        if updated:
            save_state(state, sp)
        return updated
    except (ImportError, OSError, TypeError, ValueError):
        return None


def _zone_show(args: argparse.Namespace):
    """Show zone classifications for all scanned files."""
    state_file = state_path(args)
    if not state_file.exists():
        raise CommandError("No state file found — run a scan first.")
    lang = resolve_lang(args)
    if not lang or not lang.file_finder:
        raise CommandError("No language detected — run a scan first.")

    path = Path(args.path)
    overrides = command_runtime(args).config.get("zone_overrides", {})

    files = lang.file_finder(path)
    zone_map = FileZoneMap(
        files, lang.zone_rules, rel_fn=rel, overrides=overrides or None
    )

    # Group files by zone
    by_zone: dict[str, list[str]] = {}
    for f in sorted(files, key=lambda f: rel(f)):
        zone = zone_map.get(f)
        by_zone.setdefault(zone.value, []).append(f)

    total = len(files)
    print(colorize(f"\nZone classifications ({total} files)\n", "bold"))

    decided_by: dict[str, int] = {}
    for zone_val in [zone.value for zone in Zone]:
        zone_files = by_zone.get(zone_val, [])
        if not zone_files:
            continue
        print(colorize(f"  {zone_val} ({len(zone_files)} files)", "bold"))
        for f in zone_files:
            rp = rel(f)
            key = matching_override(rp, overrides)
            suffix = ""
            if key == rp:
                suffix = colorize(" (override)", "cyan")
            elif key is not None:
                suffix = colorize(f" (override: {key})", "cyan")
            if key is not None:
                decided_by[key] = decided_by.get(key, 0) + 1
            print(f"    {rp}{suffix}")
        print()

    if overrides:
        print(colorize(f"  {len(overrides)} override(s) active:", "dim"))
        for key in sorted(overrides):
            count = decided_by.get(key, 0)
            files_label = "file" if count == 1 else "files"
            print(colorize(f"    {key} → {overrides[key]} ({count} {files_label})", "dim"))
    print(colorize("  Override: desloppify zone set <file|dir|'glob'> <zone>", "dim"))
    print(colorize("  Clear:    desloppify zone clear <file|dir|'glob'>", "dim"))
    print_agent_plan(
        ["Fix misclassified files, then re-scan."],
        next_command="desloppify scan",
    )


def _zone_set(args: argparse.Namespace):
    """Set a zone override for a file, a directory, or a glob pattern."""
    zone_value = args.zone_value

    # Validate zone value
    valid_zones = {z.value for z in Zone}
    if zone_value not in valid_zones:
        raise CommandError(
            f"Invalid zone: {zone_value}. Valid: {', '.join(sorted(valid_zones))}"
        )

    key = _override_key(args.zone_path)
    config = command_runtime(args).config
    overrides = config.setdefault("zone_overrides", {})
    overrides[key] = zone_value
    try:
        config_mod.save_config(config)
    except OSError as e:
        raise CommandError(f"could not save config: {e}") from e
    kind = " (pattern)" if is_override_pattern(key) else ""
    print(f"  Set {key}{kind} → {zone_value}")

    # Apply immediately to state
    updated = _restamp_issues(
        args,
        lambda file: zone_value if matching_override(file, overrides) == key else None,
    )
    if updated is None:
        print(colorize("  (Will apply on next scan.)", "dim"))
    else:
        print(f"  Applied to {updated} issue(s).")


def _zone_clear(args: argparse.Namespace):
    """Clear a zone override for a file, a directory, or a glob pattern."""
    key = _override_key(args.zone_path)
    config = command_runtime(args).config
    overrides = config.get("zone_overrides", {})
    if key not in overrides:
        print(colorize(f"  No override found for {key}", "yellow"))
        covering = matching_override(key, overrides)
        if covering is not None:
            print(colorize(f"  It is covered by {covering}; clear that instead.", "dim"))
        return

    before = dict(overrides)
    del overrides[key]
    try:
        config_mod.save_config(config)
    except OSError as e:
        raise CommandError(f"could not save config: {e}") from e
    print(f"  Cleared override for {key}")

    # Issues it decided fall back to the next override, else production until
    # the next scan reclassifies them.
    def _fallback(file: str) -> str | None:
        if matching_override(file, before) != key:
            return None
        next_key = matching_override(file, overrides)
        return overrides[next_key] if next_key is not None else "production"

    updated = _restamp_issues(args, _fallback)
    if updated is None:
        print(colorize("  (Will apply on next scan.)", "dim"))
    else:
        print(f"  Re-stamped {updated} issue(s) (will reclassify on next scan).")
