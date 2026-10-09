"""config command: show/set/unset project configuration."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.command_runtime import command_runtime
from desloppify.app.commands.helpers.state_persistence import save_state_or_exit
from desloppify.base.config import (
    CONFIG_SCHEMA,
    save_config,
    set_config_value,
    unset_config_value,
)
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.engine._state.disabled import apply_disabled, canonical_disabled_entry
from desloppify.engine._state.schema import utc_now
from desloppify.state_score_snapshot import score_snapshot


def cmd_config(args: argparse.Namespace) -> None:
    """Handle config subcommands: show, set, unset."""
    action = getattr(args, "config_action", None)
    if action == "set":
        _config_set(args)
    elif action == "unset":
        _config_unset(args)
    else:
        _config_show(args)


def _config_show(args: argparse.Namespace):
    """Print all config keys with current values and descriptions."""
    config = command_runtime(args).config

    print(colorize("\n  Desloppify Configuration\n", "bold"))
    for key, schema in CONFIG_SCHEMA.items():
        value = config.get(key, schema.default)
        is_default = value == schema.default

        # Format display value
        if schema.type is int and key.endswith("_days") and value == 0:
            display = "never (0)"
        elif isinstance(value, list):
            display = ", ".join(value) if value else "(empty)"
        elif isinstance(value, dict):
            display = f"{len(value)} entries" if value else "(empty)"
        else:
            display = str(value)

        default_tag = colorize(" (default)", "dim") if is_default else ""
        print(f"  {key:<25} {display}{default_tag}")
        print(colorize(f"  {'':25} {schema.description}", "dim"))
    print()


def _config_set(args: argparse.Namespace):
    """Set a config key to a value."""
    runtime = command_runtime(args)
    config = runtime.config
    key = args.config_key
    value = args.config_value

    try:
        if key == "disabled":
            value = canonical_disabled_entry(value)
        elif key == "presets":
            value = _known_preset(value)
        set_config_value(config, key, value)
    except (KeyError, ValueError) as e:
        raise CommandError(str(e)) from e

    try:
        save_config(config)
    except OSError as e:
        raise CommandError(f"could not save config: {e}") from e
    display = config[key]
    if isinstance(display, int) and key.endswith("_days") and display == 0:
        display = "never (0)"
    print(colorize(f"  Set {key} = {display}", "green"))
    if key == "disabled":
        _apply_disabled_to_state(runtime)
    elif key == "presets":
        print(colorize("  Run `desloppify scan` to apply it.", "dim"))


def _known_preset(value: str) -> str:
    from desloppify.languages.typescript.presets import preset_catalog

    catalog = preset_catalog()
    name = value.strip().lower()
    if name not in catalog:
        known = "\n".join(f"    {key:<20} {desc}" for key, desc in catalog.items())
        raise ValueError(f"Unknown preset '{value}'. Known presets:\n{known}")
    return name


def _config_unset(args: argparse.Namespace):
    """Reset a config key to its default, or remove one value from a list key."""
    runtime = command_runtime(args)
    config = runtime.config
    key = args.config_key
    value = getattr(args, "config_value", None)

    try:
        unset_config_value(config, key, value)
    except (KeyError, ValueError) as e:
        raise CommandError(str(e)) from e

    try:
        save_config(config)
    except OSError as e:
        raise CommandError(f"could not save config: {e}") from e
    if value is not None:
        print(colorize(f"  Removed {value} from {key}", "green"))
    else:
        default = CONFIG_SCHEMA[key].default
        print(colorize(f"  Reset {key} to default ({default})", "green"))
    if key == "disabled":
        _apply_disabled_to_state(runtime, enabling=True)


def _apply_disabled_to_state(runtime, *, enabling: bool = False) -> None:
    """Rescore the saved state now, so status and next reflect the change."""
    state = runtime.state
    if not state.get("last_scan"):
        return
    before = score_snapshot(state)
    hidden, restored = apply_disabled(
        state, runtime.config.get("disabled", []), utc_now()
    )
    save_state_or_exit(state, runtime.state_path)
    after = score_snapshot(state)
    if hidden:
        print(f"  {hidden} issue(s) of disabled detectors hidden (status unchanged).")
    if restored:
        print(f"  {restored} issue(s) shown again; the next scan rechecks them.")
    if enabling:
        print(
            colorize(
                "  Run `desloppify scan` to score the re-enabled detectors.", "dim"
            )
        )
    if before.strict is not None and after.strict is not None:
        print(f"  Strict score: {before.strict:.1f} → {after.strict:.1f}")
