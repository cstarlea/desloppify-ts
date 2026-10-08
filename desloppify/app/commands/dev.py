"""Developer utilities."""

from __future__ import annotations

import argparse

from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize


def cmd_dev(args: argparse.Namespace) -> None:
    """Dispatch developer subcommands."""
    action = getattr(args, "dev_action", None)
    if action == "test-hermes":
        _cmd_test_hermes()
        return
    raise CommandError("Unknown dev action. Use `desloppify dev test-hermes`.")


def _cmd_test_hermes() -> None:
    """Test Hermes model switching — switch to a random model and back."""
    import random
    import time

    from desloppify.app.commands.helpers.transition_messages import (
        _hermes_available,
        _hermes_get,
        _hermes_send_message,
    )

    if not _hermes_available():
        print(colorize('Hermes not enabled. Set "hermes_enabled": true in config.json', "yellow"))
        return

    # Get current model
    info = _hermes_get("/sessions/_any")
    if "error" in info:
        print(colorize(f"Cannot reach Hermes: {info['error']}", "red"))
        return

    original_model = info.get("model", "unknown")
    original_provider = info.get("provider", "unknown")
    print(f"  Current model: {original_provider}:{original_model}")

    # Pick a random test model
    test_models = [
        ("openrouter", "google/gemini-2.5-flash"),
        ("openrouter", "meta-llama/llama-4-scout"),
        ("openrouter", "mistralai/mistral-medium-3"),
    ]
    test_provider, test_model = random.choice(test_models)

    # Switch to test model
    print(f"  Switching to: {test_provider}:{test_model}")
    result = _hermes_send_message(f"/model {test_provider}:{test_model}", mode="queue")
    if not result.get("success"):
        print(colorize(f"  Switch failed: {result.get('error', '?')}", "red"))
        return
    print(colorize("  ✓ Switch command sent", "green"))

    # Wait a moment, then switch back
    time.sleep(2)
    print(f"  Switching back to: {original_provider}:{original_model}")
    result = _hermes_send_message(f"/model {original_provider}:{original_model}", mode="queue")
    if not result.get("success"):
        print(colorize(f"  Switch-back failed: {result.get('error', '?')}", "red"))
        return
    print(colorize("  ✓ Restored original model", "green"))
