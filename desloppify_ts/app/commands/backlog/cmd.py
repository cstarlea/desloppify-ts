"""backlog command: show open backlog items outside the execution queue."""

from __future__ import annotations

import argparse

from desloppify_ts.app.commands.helpers.command_runtime import command_runtime
from desloppify_ts.app.commands.helpers.lang import resolve_lang
from desloppify_ts.app.commands.helpers.query import write_query
from desloppify_ts.app.commands.helpers.state import require_issue_inventory
from desloppify_ts.app.commands.next.queue_flow import (
    BACKLOG_QUEUE_VIEW,
    QueueRenderDeps,
    build_and_render_queue,
)
from desloppify_ts.base.output.terminal import colorize
from desloppify_ts.base.tooling import check_config_staleness
from desloppify_ts.engine.plan_state import load_plan
from desloppify_ts.engine.planning.queue_policy import build_backlog_queue


def cmd_backlog(args: argparse.Namespace) -> None:
    """Show backlog items that are not currently part of the execution queue."""
    runtime = command_runtime(args)
    state = runtime.state
    config = runtime.config
    if not require_issue_inventory(state):
        return

    config_warning = check_config_staleness(config)
    if config_warning:
        print(colorize(f"  {config_warning}", "yellow"))

    build_and_render_queue(
        args,
        state,
        config,
        view=BACKLOG_QUEUE_VIEW,
        deps=QueueRenderDeps(
            resolve_lang_fn=resolve_lang,
            load_plan_fn=load_plan,
            build_work_queue_fn=build_backlog_queue,
            write_query_fn=write_query,
        ),
    )


__all__ = [
    "BACKLOG_QUEUE_VIEW",
    "QueueRenderDeps",
    "build_and_render_queue",
    "cmd_backlog",
]
