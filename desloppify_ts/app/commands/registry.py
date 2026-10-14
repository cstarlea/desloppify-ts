"""Central command registry for CLI command handler resolution."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import lru_cache

CommandHandler = Callable[[argparse.Namespace], None]


def _build_handlers() -> dict[str, CommandHandler]:
    """Import all command modules and build the handler dict on first access."""
    from desloppify_ts.app.commands.autofix import cmd_autofix
    from desloppify_ts.app.commands.backlog import cmd_backlog
    from desloppify_ts.app.commands.config import cmd_config
    from desloppify_ts.app.commands.detect import cmd_detect
    from desloppify_ts.app.commands.directives import cmd_directives
    from desloppify_ts.app.commands.exclude import cmd_exclude
    from desloppify_ts.app.commands.move import cmd_move
    from desloppify_ts.app.commands.next import cmd_next
    from desloppify_ts.app.commands.plan import cmd_plan
    from desloppify_ts.app.commands.review import cmd_review
    from desloppify_ts.app.commands.scan import cmd_scan
    from desloppify_ts.app.commands.setup import cmd_setup
    from desloppify_ts.app.commands.show import cmd_show
    from desloppify_ts.app.commands.status import cmd_status
    from desloppify_ts.app.commands.suppress import cmd_suppress
    from desloppify_ts.app.commands.update_skill import cmd_update_skill
    from desloppify_ts.app.commands.viz import cmd_tree, cmd_viz
    from desloppify_ts.app.commands.zone import cmd_zone

    return {
        "scan": cmd_scan,
        "setup": cmd_setup,
        "status": cmd_status,
        "show": cmd_show,
        "next": cmd_next,
        "backlog": cmd_backlog,
        "suppress": cmd_suppress,
        "exclude": cmd_exclude,
        "autofix": cmd_autofix,
        "plan": cmd_plan,
        "detect": cmd_detect,
        "tree": cmd_tree,
        "viz": cmd_viz,
        "move": cmd_move,
        "zone": cmd_zone,
        "review": cmd_review,
        "config": cmd_config,
        "directives": cmd_directives,
        "update-skill": cmd_update_skill,
    }


@lru_cache(maxsize=1)
def get_command_handlers() -> dict[str, CommandHandler]:
    """Return cached command handler dict, building on first access."""
    return _build_handlers()


__all__ = ["CommandHandler", "get_command_handlers"]
