"""Line scanner for generic cross-language security checks."""

from __future__ import annotations

from typing import Any

from .rules import (
    _insecure_random_entries,
    _secret_format_entries,
    _secret_name_entries,
    _sensitive_log_entries,
    _weak_crypto_entries,
)


def _scan_line_for_security_entries(
    *,
    filepath: str,
    line_num: int,
    line: str,
    is_test: bool,
    code: str | None = None,
    uncommented: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate one source line against all generic security checks.

    ``code`` and ``uncommented`` are the line with its comments and literals
    blanked, and with only its comments blanked, when the language's lexer
    knows them. Secret formats match anywhere: a key in a string or comment
    is still leaked.
    """
    entries: list[dict[str, Any]] = []
    entries.extend(_secret_format_entries(filepath, line_num, line, is_test))
    if code is None:
        entries.extend(_secret_name_entries(filepath, line_num, line, is_test))
        entries.extend(_insecure_random_entries(filepath, line_num, line))
        entries.extend(_weak_crypto_entries(filepath, line_num, line))
        entries.extend(_sensitive_log_entries(filepath, line_num, line))
        return entries
    entries.extend(_secret_name_entries(filepath, line_num, line, is_test, code=code))
    entries.extend(
        _insecure_random_entries(
            filepath, line_num, line, code=code, uncommented=uncommented
        )
    )
    entries.extend(_weak_crypto_entries(filepath, line_num, line, code=code))
    entries.extend(
        _sensitive_log_entries(
            filepath, line_num, line, code=code, uncommented=uncommented
        )
    )
    return entries
