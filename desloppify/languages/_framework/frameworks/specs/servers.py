"""Node server framework specs: Express, Hono and Fastify."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.servers import (
    autoload_dirs,
    honox_entries,
    scan_express_misshapen_error_handlers,
    scan_express_unhandled_async_handlers,
    scan_fastify_async_with_done,
    scan_hono_unawaited_next,
    scan_hono_unreturned_responses,
    wrangler_main,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule

_SOURCE_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs"})
_VITE_CONFIGS = ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs")


def _line_issue(detector: str, rule: str, tier: int, confidence: str, summary: str):
    return lambda entry: make_issue(
        detector,
        entry["file"],
        f"{rule}::{entry['line']}",
        tier=tier,
        confidence=confidence,
        summary=summary,
        detail={"line": entry["line"]},
    )


EXPRESS_SPEC = FrameworkSpec(
    id="express",
    label="Express",
    ecosystem="node",
    detection=DetectionConfig(dependencies=("express",)),
    scanners=(
        ScannerRule(
            id="unhandled_async_handler",
            scan=lambda root, _lang: scan_express_unhandled_async_handlers(root),
            issue_factory=_line_issue(
                "express",
                "unhandled_async_handler",
                2,
                "medium",
                "Async route handler awaits without try/catch; Express 4 drops the "
                "rejection, so the request hangs instead of reaching the error handler.",
            ),
            log_message=lambda count: (
                f"       express: {count} async handlers can reject unhandled (Express 4)"
            ),
        ),
        ScannerRule(
            id="error_handler_arity",
            scan=lambda root, _lang: scan_express_misshapen_error_handlers(root),
            issue_factory=lambda entry: make_issue(
                "express",
                entry["file"],
                f"error_handler_arity::{entry['line']}",
                tier=2,
                confidence="high",
                summary=(
                    f"Error middleware takes ({entry['param']}, req, res) — Express only "
                    "treats a four-parameter function as an error handler."
                ),
                detail={"line": entry["line"]},
            ),
            log_message=lambda count: (
                f"       express: {count} error handlers miss the fourth (next) parameter"
            ),
        ),
    ),
)


# HonoX loads routes and islands from the file system; app/client.ts and
# app/server.ts are its client and server entries.
HONOX_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_VITE_CONFIGS,
    extensions=_SOURCE_EXTENSIONS,
    declared_entries=honox_entries,
)

# Cloudflare Workers, where most Hono apps run: wrangler loads its ``main``.
WRANGLER_ENTRY_CONVENTIONS = EntryConventions(
    config_files=("wrangler.toml", "wrangler.json", "wrangler.jsonc"),
    extensions=_SOURCE_EXTENSIONS,
    declared_entries=wrangler_main,
)

HONO_SPEC = FrameworkSpec(
    id="hono",
    label="Hono",
    ecosystem="node",
    detection=DetectionConfig(dependencies=("hono", "honox")),
    scanners=(
        ScannerRule(
            id="unreturned_response",
            scan=lambda root, _lang: scan_hono_unreturned_responses(root),
            issue_factory=lambda entry: make_issue(
                "hono",
                entry["file"],
                f"unreturned_response::{entry['line']}",
                tier=2,
                confidence="high",
                summary=(
                    f"Handler builds a response with c.{entry['helper']}() but doesn't "
                    "return it, so Hono never sends it."
                ),
                detail={"line": entry["line"], "helper": entry["helper"]},
            ),
            log_message=lambda count: f"       hono: {count} handlers drop the response they build",
        ),
        ScannerRule(
            id="unawaited_next",
            scan=lambda root, _lang: scan_hono_unawaited_next(root),
            issue_factory=_line_issue(
                "hono",
                "unawaited_next",
                2,
                "high",
                "Middleware calls next() without await or return, so its code after "
                "next() runs before the handlers downstream finish.",
            ),
            log_message=lambda count: f"       hono: {count} middleware calls of next() not awaited",
        ),
    ),
    entry_conventions=(HONOX_ENTRY_CONVENTIONS, WRANGLER_ENTRY_CONVENTIONS),
)


# @fastify/autoload registers every file in the directories it is pointed at.
FASTIFY_ENTRY_CONVENTIONS = EntryConventions(
    config_files=(),
    extensions=_SOURCE_EXTENSIONS,
    marker_dependencies=("@fastify/autoload",),
    declared_entries=autoload_dirs,
)

FASTIFY_SPEC = FrameworkSpec(
    id="fastify",
    label="Fastify",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("fastify",),
        script_pattern=r"(?:^|\s)fastify\s+start\b",
    ),
    scanners=(
        ScannerRule(
            id="async_with_done",
            scan=lambda root, _lang: scan_fastify_async_with_done(root),
            issue_factory=_line_issue(
                "fastify",
                "async_with_done",
                2,
                "high",
                "Async plugin or hook also takes a done callback; Fastify rejects the "
                "mix — an async function signals completion by resolving.",
            ),
            log_message=lambda count: f"       fastify: {count} async plugins/hooks also take done",
        ),
    ),
    entry_conventions=FASTIFY_ENTRY_CONVENTIONS,
)


__all__ = [
    "EXPRESS_SPEC",
    "FASTIFY_ENTRY_CONVENTIONS",
    "FASTIFY_SPEC",
    "HONOX_ENTRY_CONVENTIONS",
    "HONO_SPEC",
    "WRANGLER_ENTRY_CONVENTIONS",
]
