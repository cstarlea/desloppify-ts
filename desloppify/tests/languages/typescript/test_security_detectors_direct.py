"""Direct tests for TypeScript security helper modules."""

from __future__ import annotations

import desloppify.languages.typescript.detectors.security.entries as entries_mod
import desloppify.languages.typescript.detectors.security.file_checks as file_checks_mod
import desloppify.languages.typescript.detectors.security.line_checks as line_checks_mod
from desloppify.languages.typescript.detectors.security.patterns import public_secret_re
from desloppify.languages.typescript.syntax.scanner import SourceText


def _kinds(entries: list[dict[str, object]]) -> set[str]:
    return {str(item.get("detail", {}).get("kind", "")) for item in entries}


def _line_issues(lines: list[str], line_num: int, filepath: str, normalized_path: str, *, has_dev_guard: bool = False):
    return line_checks_mod._line_security_issues(
        filepath=filepath,
        normalized_path=normalized_path,
        source=SourceText("\n".join(lines)),
        line_num=line_num,
        is_server_only=False,
        has_dev_guard=has_dev_guard,
        public_secret=public_secret_re(("VITE_",)),
    )


def test_entries_make_security_entry_wraps_security_rule_payload() -> None:
    entry = entries_mod._make_security_entry(
        "src/app.ts",
        7,
        "eval(userInput)",
        check_id="eval_injection",
        summary="eval use",
        severity="critical",
        confidence="high",
        remediation="remove eval",
    )

    assert entry["file"] == "src/app.ts"
    assert entry["detail"]["kind"] == "eval_injection"
    assert entry["detail"]["line"] == 7
    assert entry["detail"]["severity"] == "critical"


def test_file_checks_cover_edge_auth_json_parse_and_rls_detection() -> None:
    edge_content = (
        "Deno.serve(async (req) => {\n"
        "  const payload = JSON.parse(req.body)\n"
        "  return new Response(payload)\n"
        "})\n"
    )
    edge_source = SourceText(edge_content)

    assert file_checks_mod._looks_like_edge_handler("/src/functions/handler.ts", edge_content)
    assert not file_checks_mod._looks_like_edge_handler("/src/web/handler.ts", edge_content)
    assert file_checks_mod._extract_handler_body(edge_source) is not None
    assert not file_checks_mod._handler_has_auth_check(edge_source)
    assert file_checks_mod._handler_has_auth_check(SourceText("requireAuth(user)"))
    assert not file_checks_mod._handler_has_auth_check(SourceText("// requireAuth(user)"))

    json_lines = [
        "async function parse() {",
        "  try {",
        "    JSON.parse(a)",
        "  } catch (e) {}",
        "}",
        "function plain() {",
        "  JSON.parse(b)",
        "  // JSON.parse(c)",
        "  JSON.parse(JSON.stringify(d))",
        "}",
    ]
    assert file_checks_mod._is_in_try_scope(json_lines, 3) is True
    assert file_checks_mod._is_in_try_scope(json_lines, 7) is False

    json_entries: list[dict[str, object]] = []
    file_checks_mod._check_json_parse_unguarded("src/parse.ts", SourceText("\n".join(json_lines)), json_entries)
    assert _kinds(json_entries) == {"json_parse_unguarded"}
    assert json_entries[0]["detail"]["line"] == 7

    combined = file_checks_mod._file_level_security_issues(
        filepath="/src/functions/handler.ts",
        normalized_path="/src/functions/handler.ts",
        source=edge_source,
    )
    assert {"edge_function_missing_auth", "json_parse_unguarded"} <= _kinds(combined)


def test_line_checks_report_expected_security_kinds() -> None:
    service_role_lines = [
        "const serviceRole = process.env.SUPABASE_SERVICE_ROLE_KEY",
        "const client = createClient(url, serviceRole)",
    ]
    service_role_issues = _line_issues(service_role_lines, 2, "src/client.ts", "/src/client.ts")
    assert "service_role_on_client" in _kinds(service_role_issues)

    eval_line = "const fn = new Function('a', body)"
    eval_issues = _line_issues([eval_line], 1, "src/eval.ts", "/src/eval.ts")
    assert "eval_injection" in _kinds(eval_issues)

    html_line = "node.innerHTML = payload.dangerouslySetInnerHTML"
    html_issues = _line_issues([html_line], 1, "src/dom.ts", "/src/dom.ts")
    assert {"dangerously_set_inner_html", "innerHTML_assignment"} <= _kinds(html_issues)

    dev_cred_line = "const token = import.meta.env.VITE_API_TOKEN"
    dev_cred_issues = _line_issues([dev_cred_line], 1, "src/app.ts", "/src/app.ts")
    assert "dev_credentials_env" in _kinds(dev_cred_issues)

    guarded_dev_issues = _line_issues([dev_cred_line], 1, "src/dev.client.ts", "/src/dev/client.ts", has_dev_guard=True)
    assert guarded_dev_issues == []

    redirect_line = "window.location = data.nextUrl"
    redirect_issues = _line_issues([redirect_line], 1, "src/redirect.ts", "/src/redirect.ts")
    assert "open_redirect" in _kinds(redirect_issues)

    jwt_lines = [
        "const payload = token.split('.')",
        "const decoded = atob(payload[1])",
    ]
    jwt_issues = _line_issues(jwt_lines, 2, "src/auth.ts", "/src/auth.ts")
    assert "unverified_jwt_decode" in _kinds(jwt_issues)
