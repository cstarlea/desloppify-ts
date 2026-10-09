"""Security issue rule metadata and issue builders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from desloppify.base.discovery.file_paths import rel
from desloppify.engine.detectors.patterns.security import LOG_CALLS as _LOG_CALLS
from desloppify.engine.detectors.patterns.security import RANDOM_CALLS as _RANDOM_CALLS
from desloppify.engine.detectors.patterns.security import (
    SECRET_FORMAT_PATTERNS as _SECRET_FORMAT_PATTERNS,
)
from desloppify.engine.detectors.patterns.security import (
    SECRET_NAME_RE as _SECRET_NAME_RE,
)
from desloppify.engine.detectors.patterns.security import SECRET_NAMES as _SECRET_NAMES
from desloppify.engine.detectors.patterns.security import (
    SECURITY_CONTEXT_WORDS as _SECURITY_CONTEXT_WORDS,
)
from desloppify.engine.detectors.patterns.security import (
    SENSITIVE_IN_LOG as _SENSITIVE_IN_LOG,
)
from desloppify.engine.detectors.patterns.security import (
    WEAK_CRYPTO_PATTERNS as _WEAK_CRYPTO_PATTERNS,
)
from desloppify.engine.detectors.patterns.security import (
    is_env_lookup as _is_env_lookup,
)
from desloppify.engine.detectors.patterns.security import (
    is_placeholder as _is_placeholder,
)

_LOGGED_VALUE = re.compile(r"[\w$]")


@dataclass(frozen=True)
class SecurityRule:
    """Metadata describing one detector issue shape."""

    check_id: str
    summary: str
    severity: str
    confidence: str
    remediation: str


def make_security_entry(
    filepath: str,
    line: int,
    content: str,
    rule: SecurityRule,
) -> dict[str, Any]:
    """Build a security issue entry dict."""
    rel_path = rel(filepath)
    return {
        "file": filepath,
        "name": f"security::{rule.check_id}::{rel_path}::{line}",
        "tier": 2,
        "confidence": rule.confidence,
        "summary": rule.summary,
        "detail": {
            "kind": rule.check_id,
            "severity": rule.severity,
            "line": line,
            "content": content[:200],
            "remediation": rule.remediation,
        },
    }


def _secret_format_entries(
    filepath: str,
    line_num: int,
    line: str,
    is_test: bool,
) -> list[dict[str, Any]]:
    confidence = "medium" if is_test else "high"
    entries: list[dict[str, Any]] = []
    for label, pattern, severity, remediation in _SECRET_FORMAT_PATTERNS:
        if not pattern.search(line):
            continue
        entries.append(
            make_security_entry(
                filepath,
                line_num,
                line,
                SecurityRule(
                    check_id="hardcoded_secret_value",
                    summary=f"Hardcoded {label} detected",
                    severity=severity,
                    confidence=confidence,
                    remediation=remediation,
                ),
            )
        )
    return entries


def _in_code(code: str | None, line: str, offset: int) -> bool:
    """Whether ``line[offset]`` is code, given the line with its literals and
    comments blanked (``code``); without one, every offset counts."""
    return code is None or (offset < len(code) and code[offset] == line[offset] != " ")


def _secret_name_entries(
    filepath: str,
    line_num: int,
    line: str,
    is_test: bool,
    *,
    code: str | None = None,
) -> list[dict[str, Any]]:
    confidence = "medium" if is_test else "high"
    entries: list[dict[str, Any]] = []
    for secret_match in _SECRET_NAME_RE.finditer(line):
        # The name is code (so the quote after its ``=`` or ``:`` opens a
        # literal), not words in a comment, string or JSX text.
        if not _in_code(code, line, secret_match.start(1)):
            continue
        var_name = secret_match.group(1)
        value = secret_match.group(3)
        if not _SECRET_NAMES.search(var_name):
            continue
        if _is_env_lookup(line):
            continue
        if _is_placeholder(value):
            continue
        entries.append(
            make_security_entry(
                filepath,
                line_num,
                line,
                SecurityRule(
                    check_id="hardcoded_secret_name",
                    summary=f"Hardcoded secret in variable '{var_name}'",
                    severity="high",
                    confidence=confidence,
                    remediation="Move secret to environment variable or secrets manager",
                ),
            )
        )
    return entries


def _insecure_random_entries(
    filepath: str,
    line_num: int,
    line: str,
    *,
    code: str | None = None,
    uncommented: str | None = None,
) -> list[dict[str, Any]]:
    # The call is code; the context may be a name or a string key
    # (``setItem('session', Math.random())``), but not a comment.
    if not (
        _RANDOM_CALLS.search(line if code is None else code)
        and _SECURITY_CONTEXT_WORDS.search(line if uncommented is None else uncommented)
    ):
        return []
    return [
        make_security_entry(
            filepath,
            line_num,
            line,
            SecurityRule(
                check_id="insecure_random",
                summary="Insecure random used in security context",
                severity="medium",
                confidence="medium",
                remediation="Use secrets.token_hex() (Python), crypto.randomUUID() (JS), or random_bytes() (PHP)",
            ),
        )
    ]


def _weak_crypto_entries(
    filepath: str,
    line_num: int,
    line: str,
    *,
    code: str | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for pattern, label, severity, remediation in _WEAK_CRYPTO_PATTERNS:
        # A setting starts in code; its value may be a string (``= '0'``).
        if not any(
            _in_code(code, line, match.start()) for match in pattern.finditer(line)
        ):
            continue
        entries.append(
            make_security_entry(
                filepath,
                line_num,
                line,
                SecurityRule(
                    check_id="weak_crypto_tls",
                    summary=label,
                    severity=severity,
                    confidence="high",
                    remediation=remediation,
                ),
            )
        )
    return entries


def _sensitive_log_entries(
    filepath: str,
    line_num: int,
    line: str,
    *,
    code: str | None = None,
    uncommented: str | None = None,
) -> list[dict[str, Any]]:
    log_call = _LOG_CALLS.search(line if code is None else code)
    if log_call is None or not _SENSITIVE_IN_LOG.search(
        line if uncommented is None else uncommented
    ):
        return []
    # A sensitive word only in a string counts when the call logs a value
    # too (``console.log("Authorization:", header)``); a message alone
    # (``console.error("Invalid token")``) only mentions one.
    if code is not None and not (
        _SENSITIVE_IN_LOG.search(code) or _LOGGED_VALUE.search(code, log_call.end())
    ):
        return []
    return [
        make_security_entry(
            filepath,
            line_num,
            line,
            SecurityRule(
                check_id="log_sensitive",
                summary="Sensitive data may be logged",
                severity="medium",
                confidence="medium",
                remediation="Remove sensitive data from log statements",
            ),
        )
    ]
