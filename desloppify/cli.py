"""CLI entry point: parse args, load shared context, dispatch command handlers."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from desloppify.app.cli_support.parser import create_parser as _create_parser
from desloppify.app.commands.helpers.command_lock import command_lock
from desloppify.app.commands.helpers.command_runtime import CommandRuntime
from desloppify.app.commands.helpers.lang import resolve_lang
from desloppify.app.commands.helpers.state import state_path
from desloppify.app.commands.registry import CommandHandler, get_command_handlers
from desloppify.base.config import load_config
from desloppify.base.discovery.paths import get_default_scan_path, get_project_root
from desloppify.base.discovery.source import set_exclusions
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.cli_logging import configure_cli_logging
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize
from desloppify.base.registry import detector_names
from desloppify.base.runtime_state import runtime_scope
from desloppify.state_io import load_state

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_detector_names_cached() -> tuple[str, ...]:
    """Compute detector names once."""
    return tuple(detector_names())


def _get_detector_names() -> list[str]:
    """Return cached detector names, computing on first access."""
    return list(_get_detector_names_cached())


def create_parser() -> argparse.ArgumentParser:
    """Return the top-level argparse parser."""
    return _create_parser(detector_names=_get_detector_names())


def _apply_persisted_exclusions(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> None:
    """Merge CLI --exclude with persisted config.exclude and apply globally."""
    cli_exclusions = getattr(args, "exclude", None) or []
    persisted_raw = config.get("exclude", [])
    persisted = (
        [entry for entry in persisted_raw if isinstance(entry, str)]
        if isinstance(persisted_raw, list)
        else []
    )
    combined = list(cli_exclusions) + [e for e in persisted if e not in cli_exclusions]
    if not combined:
        return
    set_exclusions(combined)
    if cli_exclusions:
        print(
            colorize(f"  Excluding: {', '.join(combined)}", "dim"),
            file=sys.stderr,
        )
        return
    print(
        colorize(
            f"  Excluding (from config): {', '.join(combined)}", "dim"
        ),
        file=sys.stderr,
    )


def _project_root_from_state_path(state_path_value: str | Path | None) -> Path | None:
    """Infer project root from an explicit state path under ``.desloppify``."""
    if state_path_value in (None, ""):
        return None
    try:
        state_file = Path(state_path_value).resolve()
    except OSError:
        return None
    if state_file.parent.name != ".desloppify":
        return None
    if state_file.name == "state.json" or (
        state_file.name.startswith("state-") and state_file.suffix == ".json"
    ):
        return state_file.parent.parent
    return None


def _is_submodule_checkout(directory: Path) -> bool:
    """True when ``directory/.git`` is a gitfile pointing into ``.git/modules``.

    Worktrees also use a gitfile (pointing into ``.git/worktrees``); they are
    independent checkouts, so only submodules count.
    """
    try:
        first = (directory / ".git").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return False
    if not first.startswith("gitdir:"):
        return False
    gitdir = Path(first[len("gitdir:"):].strip())
    return gitdir.parent.name != "worktrees" and "modules" in gitdir.parts


def _project_root_from_scan_path(
    scan_path_value: str | Path | None, cwd_root: Path
) -> Path | None:
    """Infer the project root from an explicit ``--path``.

    The nearest ancestor of the path (inclusive) holding desloppify state or a
    git checkout (``.git`` directory, or a worktree's ``.git`` file) wins, so a
    repo nested inside another project with state keeps its own state. A git
    submodule stays with its superproject's state when the superproject has
    some, and is its own root otherwise. Without any marker, a path inside the
    cwd keeps the cwd as root (the historical behavior) and a path outside it
    is its own root, so ``desloppify scan --path ../app`` keeps its state and
    finding IDs in ``../app`` instead of the cwd.
    """
    if scan_path_value in (None, ""):
        return None
    try:
        scan_path = Path(scan_path_value).resolve()
    except OSError:
        return None
    start = scan_path if scan_path.is_dir() else scan_path.parent
    submodule: Path | None = None
    for directory in (start, *start.parents):
        if (directory / ".desloppify").is_dir():
            return directory
        if not (directory / ".git").exists():
            continue
        if _is_submodule_checkout(directory):
            submodule = submodule or directory
            continue
        if submodule is not None:
            # The superproject has no state: the submodule is its own root.
            return submodule
        return directory
    if submodule is not None:
        return submodule
    if scan_path.is_relative_to(cwd_root):
        return None
    return start


def _resolve_default_path(args: argparse.Namespace) -> None:
    """Fill args.path from the last scan's path, else the language default.

    Every command with ``--path`` (scan included) defaults to the scope of the
    last scan stored in state, so ``desloppify scan --path .`` followed by
    ``desloppify autofix ...`` or a bare ``desloppify scan`` keeps working on
    the same files even when the project is not under ``src/``. Without a
    saved scan path (or when it no longer exists) the language's default
    source directory is used.
    """
    if getattr(args, "path", None) is not None:
        return
    if not hasattr(args, "path"):
        return
    runtime_root = get_project_root()
    try:
        state_file = state_path(args)
        if state_file:
            saved = load_state(state_file)
            saved_path = saved.get("scan_path")
            if saved_path:
                resolved = (runtime_root / saved_path).resolve()
                if resolved.exists():
                    args.path = str(resolved)
                    return
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as exc:
        log_best_effort_failure(logger, "resolve default path from saved state", exc)
    lang = resolve_lang(args)
    args.path = str(
        get_default_scan_path(
            project_root=runtime_root,
            default_src=lang.default_src if lang else None,
        )
    )


def _load_shared_runtime(args: argparse.Namespace) -> None:
    """Load config/state and attach shared objects to parsed args."""
    config = load_config()

    state_file = state_path(args)
    state = load_state(state_file)
    _apply_persisted_exclusions(args, config)

    args.runtime = CommandRuntime(config=config, state=state, state_path=state_file)


def _looks_like_desloppify_checkout(root: Path) -> bool:
    """Return True when *root* appears to be the desloppify source checkout."""
    package_dir = root / "desloppify"
    pyproject = root / "pyproject.toml"
    if not package_dir.is_dir() or not (package_dir / "__init__.py").is_file():
        return False
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"""^name\s*=\s*["']desloppify(-ts)?["']""", text, re.MULTILINE))


def _running_installed_package_from_checkout(
    *,
    cwd_root: Path | None = None,
    module_file: str | Path | None = None,
) -> bool:
    """Detect running an installed package while standing in a repo checkout."""
    root = (cwd_root or get_project_root()).resolve()
    if not _looks_like_desloppify_checkout(root):
        return False
    current_module = Path(module_file or __file__).resolve()
    try:
        current_module.relative_to(root)
        return False
    except ValueError:
        return True


def _warn_if_running_installed_package_from_checkout() -> None:
    """Emit a warning when CLI resolution bypasses the current checkout."""
    if not _running_installed_package_from_checkout():
        return
    root = get_project_root().resolve()
    print(
        colorize(
            "  WARNING: running installed desloppify package while current directory "
            "looks like the desloppify checkout.",
            "yellow",
        ),
        file=sys.stderr,
    )
    print(
        colorize(
            f"  Current checkout: {root}",
            "dim",
        ),
        file=sys.stderr,
    )
    print(
        colorize(
            "  Use `python -m desloppify ...` or `./.venv/bin/desloppify ...` here "
            "to run the local checkout instead of the installed package.",
            "dim",
        ),
        file=sys.stderr,
    )


def _resolve_handler(command: str) -> CommandHandler:
    """Resolve a CLI command name to its typed handler callable."""
    return get_command_handlers()[command]


def _handle_help_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """Handle explicit help command when present in parser config."""
    topic = list(getattr(args, "topic", []) or [])
    try:
        parser.parse_args([*topic, "--help"])
    except SystemExit:
        return


def _reject_removed_lang_flag(argv: list[str]) -> None:
    """Explain the removed global ``--lang`` flag instead of a confusing parse error."""
    for token in argv:
        if token == "--":
            return
        if token == "--lang" or token.startswith("--lang="):
            print(
                "desloppify: --lang was removed; desloppify only scans "
                "TypeScript and JavaScript now, so drop the flag.",
                file=sys.stderr,
            )
            raise SystemExit(2)


def main() -> None:
    configure_cli_logging()
    # Ensure Unicode output works on Windows terminals (cp1252 etc.)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                logger.debug(
                    "Skipping stream reconfigure for %s (not supported)",
                    getattr(stream, "name", "<stream>"),
                )

    _reject_removed_lang_flag(sys.argv[1:])
    parser = create_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    if args.command == "help":
        _handle_help_command(args, parser)
        return

    try:
        with runtime_scope() as runtime:
            inferred = _project_root_from_state_path(getattr(args, "state", None))
            if inferred is None and "DESLOPPIFY_ROOT" not in os.environ:
                inferred = _project_root_from_scan_path(
                    getattr(args, "path", None), get_project_root()
                )
            if inferred is not None:
                runtime.project_root = inferred
            _warn_if_running_installed_package_from_checkout()

            # Lightweight commands that don't need state/config/exclusions.
            if args.command in {"setup", "update-skill"}:
                handler = _resolve_handler(args.command)
                handler(args)
            else:
                # Commands that may save hold the state and plan locks from
                # before the first load until they return (CE-4).
                with command_lock(args):
                    _resolve_default_path(args)
                    _load_shared_runtime(args)
                    handler = _resolve_handler(args.command)
                    handler(args)
    except CommandError as exc:
        print(colorize(f"  {exc.message}", "red"), file=sys.stderr)
        sys.exit(exc.exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
