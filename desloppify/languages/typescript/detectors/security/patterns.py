"""Regex patterns for TypeScript security detectors."""

from __future__ import annotations

import re

from desloppify.base.signal_patterns import AUTH_GUARD_TOKEN_RE

_CREATE_CLIENT_RE = re.compile(r"\bcreateClient\s*\(", re.IGNORECASE)
# Bare or global eval only: page.$eval(), redis.eval() and similar methods are
# unrelated APIs.
_EVAL_PATTERNS = re.compile(
    r"(?:(?<![\w$.])|\b(?:window|globalThis|self)\.)(?:eval|new\s+Function)\s*\("
)
_DANGEROUS_HTML_RE = re.compile(r"dangerouslySetInnerHTML")
# Clearing with an empty string is safe; comparisons (==, ===) are not writes.
_INNER_HTML_RE = re.compile(r"\.innerHTML\s*=(?!=)(?!\s*(?:''|\"\"|``)\s*;?\s*$)")
_OPEN_REDIRECT_RE = re.compile(
    r"window\.location(?:\.href)?\s*=\s*(?:data\.|response\.|params\.|query\.|\w+\[)"
)
_JSON_PARSE_RE = re.compile(r"JSON\.parse\s*\(")
_JSON_DEEP_CLONE_RE = re.compile(r"JSON\.parse\s*\(\s*JSON\.stringify\s*\(")
_SERVE_ASYNC_RE = re.compile(r"\b(?:Deno\.)?serve\s*\(\s*(?:async\s*)?")
_EDGE_ENTRYPOINT_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s+(?:GET|POST|PUT|PATCH|DELETE)\b"
)
_AUTH_CHECK_RE = AUTH_GUARD_TOKEN_RE
_ATOB_JWT_RE = re.compile(r"atob\s*\(")
_JWT_PAYLOAD_RE = re.compile(r"(?:payload\.sub|\.split\s*\(\s*['\"]\\?\.['\"])")


def public_secret_re(prefixes: tuple[str, ...]) -> re.Pattern[str] | None:
    """A secret-named env var with a public prefix (``VITE_API_TOKEN``):
    the bundler inlines it into client code. Group 1 is the prefix."""
    names = sorted({p for p in prefixes if p}, key=len, reverse=True)
    if not names:
        return None
    alternation = "|".join(re.escape(p) for p in names)
    return re.compile(
        rf"\b({alternation})\w*(?i:(?:PASSWORD|SECRET)\w*|TOKEN|API_?KEY|PRIVATE_KEY)\b"
    )


__all__ = [
    "_ATOB_JWT_RE",
    "_AUTH_CHECK_RE",
    "_CREATE_CLIENT_RE",
    "_DANGEROUS_HTML_RE",
    "_EDGE_ENTRYPOINT_RE",
    "_EVAL_PATTERNS",
    "_INNER_HTML_RE",
    "_JSON_DEEP_CLONE_RE",
    "_JSON_PARSE_RE",
    "_JWT_PAYLOAD_RE",
    "_OPEN_REDIRECT_RE",
    "_SERVE_ASYNC_RE",
    "public_secret_re",
]
