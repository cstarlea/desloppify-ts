"""Line-level TypeScript security checks."""

from __future__ import annotations

from pathlib import Path

from desloppify.base.signal_patterns import SERVICE_ROLE_TOKEN_RE
from desloppify.languages.typescript.detectors.security.entries import _make_security_entry
from desloppify.languages.typescript.syntax.scanner import SourceText
from desloppify.languages.typescript.detectors.security.patterns import (
    _ATOB_JWT_RE,
    _CREATE_CLIENT_RE,
    _DANGEROUS_HTML_RE,
    _DEV_CRED_RE,
    _EVAL_PATTERNS,
    _INNER_HTML_RE,
    _JWT_PAYLOAD_RE,
    _OPEN_REDIRECT_RE,
)


def _line_security_issues(
    *,
    filepath: str,
    normalized_path: str,
    source: SourceText,
    line_num: int,
    is_server_only: bool,
    has_dev_guard: bool,
) -> list[dict[str, object]]:
    """Detect per-line security patterns that start in code and return issues."""
    line_issues: list[dict[str, object]] = []
    index = line_num - 1
    line = source.lines[index]

    def found(pattern, anchor: str = "code") -> bool:
        return source.search(pattern, index, anchor) is not None

    def context() -> str:
        """The lines around this one, comments blanked."""
        return "\n".join(source.uncommented_lines[max(0, index - 2) : index + 3])

    if found(_CREATE_CLIENT_RE):
        if SERVICE_ROLE_TOKEN_RE.search(context()) and not is_server_only:
            line_issues.append(
                _make_security_entry(
                    filepath,
                    line_num,
                    line,
                    check_id="service_role_on_client",
                    summary="Supabase service role key used in client code",
                    severity="critical",
                    confidence="high",
                    remediation="Never use SERVICE_ROLE key outside server-only code - use anon key + RLS on clients",
                )
            )

    if found(_EVAL_PATTERNS):
        line_issues.append(
            _make_security_entry(
                filepath,
                line_num,
                line,
                check_id="eval_injection",
                summary="eval() or new Function() - potential code injection",
                severity="critical",
                confidence="high",
                remediation="Avoid eval/new Function - use safer alternatives (JSON.parse, Map, etc.)",
            )
        )

    if found(_DANGEROUS_HTML_RE):
        line_issues.append(
            _make_security_entry(
                filepath,
                line_num,
                line,
                check_id="dangerously_set_inner_html",
                summary="dangerouslySetInnerHTML - XSS risk if data is untrusted",
                severity="high",
                confidence="medium",
                remediation="Sanitize HTML with DOMPurify before using dangerouslySetInnerHTML",
            )
        )

    if found(_INNER_HTML_RE):
        line_issues.append(
            _make_security_entry(
                filepath,
                line_num,
                line,
                check_id="innerHTML_assignment",
                summary="Direct .innerHTML assignment - XSS risk",
                severity="high",
                confidence="medium",
                remediation="Use textContent for text or sanitize HTML with DOMPurify",
            )
        )

    if found(_DEV_CRED_RE, "uncommented"):
        is_dev_file = "/dev/" in normalized_path or "dev." in Path(filepath).name
        if not (is_dev_file and has_dev_guard):
            line_issues.append(
                _make_security_entry(
                    filepath,
                    line_num,
                    line,
                    check_id="dev_credentials_env",
                    summary="Sensitive credential exposed via VITE_ environment variable",
                    severity="medium",
                    confidence="medium",
                    remediation="Sensitive credentials should never be in client-accessible VITE_ env vars",
                )
            )

    if found(_OPEN_REDIRECT_RE):
        line_issues.append(
            _make_security_entry(
                filepath,
                line_num,
                line,
                check_id="open_redirect",
                summary="Potential open redirect: user-controlled data assigned to window.location",
                severity="medium",
                confidence="medium",
                remediation="Validate redirect URLs against an allowlist before redirecting",
            )
        )

    if found(_ATOB_JWT_RE):
        if _JWT_PAYLOAD_RE.search(context()):
            line_issues.append(
                _make_security_entry(
                    filepath,
                    line_num,
                    line,
                    check_id="unverified_jwt_decode",
                    summary="JWT decoded with atob() without signature verification",
                    severity="critical",
                    confidence="high",
                    remediation="Use auth.getUser() or a JWT library that verifies signatures",
                )
            )

    return line_issues


__all__ = ["_line_security_issues"]
