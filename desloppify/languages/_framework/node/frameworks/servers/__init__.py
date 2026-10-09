"""Node server framework support (Express, Hono, Fastify) shared across JS/TS scans."""

from __future__ import annotations

from .express import (
    scan_express_misshapen_error_handlers,
    scan_express_unhandled_async_handlers,
)
from .fastify import autoload_dirs, scan_fastify_async_with_done
from .hono import (
    honox_entries,
    scan_hono_unawaited_next,
    scan_hono_unreturned_responses,
    wrangler_main,
)

__all__ = [
    "autoload_dirs",
    "honox_entries",
    "scan_express_misshapen_error_handlers",
    "scan_express_unhandled_async_handlers",
    "scan_fastify_async_with_done",
    "scan_hono_unawaited_next",
    "scan_hono_unreturned_responses",
    "wrangler_main",
]
