"""Backend security checks on the syntax tree (roadmap 3.8).

- ``sql_injection``: a raw-SQL API called with an interpolated string.
- ``shell_injection``: ``child_process.exec``/``execSync`` (or a ``shell: true``
  spawn) called with an interpolated command.
- ``server_action_missing_auth`` / ``route_handler_missing_auth``: an exported
  Next.js server action, or a mutating ``route.ts`` handler, that makes no
  recognisable auth, session or secret check.

Every check needs the tree; without tree-sitter there are none. IDs name the
function (and sink), not the line, so they survive edits above them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.languages.typescript.detectors.security.entries import (
    _make_security_entry,
)
from desloppify.languages.typescript.syntax import queries as q
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    parse_text,
    parsed_file,
)

# ── Constants and interpolation ─────────────────────────────

_CONSTANT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_LITERAL_TYPES = frozenset(
    {"string", "number", "true", "false", "null", "undefined", "regex"}
)
_FUNCTION_TYPES = frozenset(
    {"function_declaration", "function_expression", "function", "arrow_function"}
)
# ``ids.map(() => '?').join(',')``: a list of placeholders, not a value.
_NUMERIC_TYPES = frozenset({"number", "bigint", "boolean"})
_NUMERIC_CALLS = frozenset(
    {
        "Number",
        "parseInt",
        "parseFloat",
        "BigInt",
        "Number.parseInt",
        "Number.parseFloat",
    }
)
_PLACEHOLDER_LITERAL_RE = re.compile(r"""['"`]\s*(?:\?|\$\d*|:\w+)\s*['"`]""")


@dataclass
class _Scope:
    """Name lookups for one parsed file."""

    parsed: ParsedSource
    path: Path | None = None
    declarations: dict[str, list] = field(default_factory=dict)  # name -> declarators
    top_functions: dict[str, object] = field(
        default_factory=dict
    )  # name -> function node
    imported: dict[str, str] = field(default_factory=dict)  # local name -> module

    @classmethod
    def build(cls, parsed: ParsedSource, path: Path | None = None) -> _Scope:
        scope = cls(parsed, path)
        for node in q.descendants(parsed.root, ("variable_declarator",)):
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                scope.declarations.setdefault(parsed.text(name), []).append(node)
        for statement in parsed.root.named_children:
            target = (
                statement.child_by_field_name("declaration")
                if statement.type == "export_statement"
                else statement
            )
            if target is None:
                continue
            if target.type == "function_declaration":
                name = target.child_by_field_name("name")
                if name is not None:
                    scope.top_functions[parsed.text(name)] = target
            elif target.type in ("lexical_declaration", "variable_declaration"):
                for declarator in target.named_children:
                    name = declarator.child_by_field_name("name")
                    value = _function_value(declarator.child_by_field_name("value"))
                    if (
                        name is not None
                        and name.type == "identifier"
                        and value is not None
                    ):
                        scope.top_functions[parsed.text(name)] = value
        for info in q.imports(parsed):
            for binding in info.bindings:
                scope.imported[binding.local] = info.source
        return scope

    def initializer(self, name: str, use) -> object | None:
        """The value of the nearest declaration of ``name`` before ``use`` in an enclosing scope."""
        best = None
        for declarator in self.declarations.get(name, ()):
            if declarator.start_byte > use.start_byte:
                continue
            holder = _enclosing_function(declarator)
            if holder is not None and not (
                holder.start_byte <= use.start_byte < holder.end_byte
            ):
                continue
            if best is None or declarator.start_byte > best.start_byte:
                best = declarator
        return best.child_by_field_name("value") if best is not None else None


def _function_value(node):
    while node is not None and node.type in (
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
    ):
        node = node.named_children[0] if node.named_children else None
    return node if node is not None and node.type in _FUNCTION_TYPES else None


def _enclosing_function(node):
    node = node.parent
    while node is not None:
        if node.type in _FUNCTION_TYPES or node.type == "method_definition":
            return node
        node = node.parent
    return None


def _unwrap(node):
    while node is not None and node.type in (
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
        "non_null_expression",
        "await_expression",
    ):
        node = node.named_children[0] if node.named_children else None
    return node


def _is_constant(scope: _Scope, node, depth: int = 0) -> bool:
    """True when ``node`` can't carry outside input: literals, ALL_CAPS names,
    consts bound to constants, and placeholder lists."""
    node = _unwrap(node)
    if node is None or depth > 4:
        return False
    parsed = scope.parsed
    kind = node.type
    if kind in _LITERAL_TYPES:
        return True
    if kind == "template_string":
        return all(
            _is_constant(
                scope, sub.named_children[0] if sub.named_children else None, depth + 1
            )
            for sub in node.named_children
            if sub.type == "template_substitution"
        )
    if kind == "binary_expression":
        left, right = (
            node.child_by_field_name("left"),
            node.child_by_field_name("right"),
        )
        return _is_constant(scope, left, depth + 1) and _is_constant(
            scope, right, depth + 1
        )
    if kind == "ternary_expression":
        return _is_constant(
            scope, node.child_by_field_name("consequence"), depth + 1
        ) and _is_constant(scope, node.child_by_field_name("alternative"), depth + 1)
    if kind == "identifier":
        name = parsed.text(node)
        if _CONSTANT_NAME_RE.match(name):
            return True
        value = scope.initializer(name, node)
        if value is None:
            return _parameter_type(scope, name, node) in _NUMERIC_TYPES
        text = parsed.text(value)
        if ".join(" in text and _PLACEHOLDER_LITERAL_RE.search(text):
            return True
        return _is_constant(scope, value, depth + 1)
    if kind == "call_expression":
        function = node.child_by_field_name("function")
        callee = parsed.text(function) if function is not None else ""
        return callee in _NUMERIC_CALLS or callee.startswith("Math.")
    if kind == "member_expression":
        if parsed.text(node.child_by_field_name("property") or node) == "length":
            return True
        return any(
            _CONSTANT_NAME_RE.match(part) for part in parsed.text(node).split(".")[:1]
        ) or bool(
            _CONSTANT_NAME_RE.match(
                parsed.text(node.child_by_field_name("property") or node)
            )
        )
    return False


def _parameter_type(scope: _Scope, name: str, use) -> str | None:
    """The annotated type of the parameter ``name`` that ``use`` refers to."""
    holder = _enclosing_function(use)
    while holder is not None:
        params = holder.child_by_field_name("parameters")
        for param in params.named_children if params is not None else ():
            pattern = param.child_by_field_name("pattern")
            if (
                pattern is None
                or pattern.type != "identifier"
                or scope.parsed.text(pattern) != name
            ):
                continue
            annotation = param.child_by_field_name("type")
            return (
                scope.parsed.text(annotation).lstrip(":").strip()
                if annotation is not None
                else None
            )
        holder = _enclosing_function(holder)
    return None


def _interpolated(scope: _Scope, node, depth: int = 0):
    """The template or concatenation behind ``node`` when it builds a string from
    a non-constant value, else None. A local variable is followed to its value."""
    node = _unwrap(node)
    if node is None or depth > 3:
        return None
    if node.type == "template_string":
        has_sub = any(
            sub.type == "template_substitution" for sub in node.named_children
        )
        return node if has_sub and not _is_constant(scope, node) else None
    if node.type == "binary_expression" and _operator(scope, node) == "+":
        return node if _has_string(node) and not _is_constant(scope, node) else None
    if node.type == "identifier":
        value = scope.initializer(scope.parsed.text(node), node)
        return _interpolated(scope, value, depth + 1) if value is not None else None
    return None


def _operator(scope: _Scope, node) -> str:
    op = node.child_by_field_name("operator")
    return scope.parsed.text(op) if op is not None else ""


def _has_string(node) -> bool:
    """A ``+`` chain with a string or template operand (string concatenation)."""
    for side in (node.child_by_field_name("left"), node.child_by_field_name("right")):
        side = _unwrap(side)
        if side is None:
            continue
        if side.type in ("string", "template_string"):
            return True
        if side.type == "binary_expression" and _has_string(side):
            return True
    return False


def _literal_text(scope: _Scope, node) -> str:
    """The literal parts of a template or concatenation, joined (values left out)."""
    parts = []
    for child in q.descendants(node, ("string_fragment", "string")):
        if child.type == "string_fragment":
            parts.append(scope.parsed.text(child))
    return " ".join(parts)


def _callee_parts(scope: _Scope, call) -> list[str]:
    function = call.child_by_field_name("function")
    if function is None:
        return []
    text = scope.parsed.text(function).replace("?.", ".")
    text = re.sub(r"\s+", "", text)
    return text.split(".")


# ── SQL ─────────────────────────────────────────────────────

# APIs whose only job is to run a raw string as SQL.
_UNSAFE_SQL_METHODS = frozenset(
    {
        "$queryRawUnsafe",
        "$executeRawUnsafe",
        # knex
        "whereRaw",
        "orWhereRaw",
        "andWhereRaw",
        "havingRaw",
        "orHavingRaw",
        "orderByRaw",
        "groupByRaw",
        "joinRaw",
        "fromRaw",
        "selectRaw",
    }
)
# ``<receiver>.<method>`` pairs that take raw SQL (drizzle/Kysely ``sql.raw``,
# ``Prisma.raw``, ``knex.raw``, postgres.js ``sql.unsafe``).
_UNSAFE_SQL_CALLS = frozenset(
    {("sql", "raw"), ("Prisma", "raw"), ("knex", "raw"), ("sql", "unsafe")}
)
# Generic method names that run SQL on a database client; the string must look
# like a statement before they count.
_SQL_TEXT_METHODS = {
    "query": "high",
    "execute": "high",
    "prepare": "high",
    "raw": "high",
    "unsafe": "high",
    "queryRaw": "high",
    "executeRaw": "high",
    "exec": "medium",
    "run": "medium",
    "all": "medium",
    "get": "medium",
}
_SQL_STATEMENT_RE = re.compile(
    r"^\s*\(?\s*(?:"
    r"SELECT\b.*\bFROM\b|INSERT\s+(?:OR\s+\w+\s+)?INTO\b|UPDATE\b.*\bSET\b|DELETE\s+FROM\b"
    r"|WITH\s+\w+(?:\s*\([^)]*\))?\s+AS\s*\(|CREATE\s+(?:TABLE|INDEX|VIEW|UNIQUE)\b|DROP\s+(?:TABLE|INDEX|VIEW)\b"
    r"|ALTER\s+TABLE\b|TRUNCATE\b|MERGE\s+INTO\b|REPLACE\s+INTO\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _sql_argument(scope: _Scope, args: list):
    """The SQL string argument: the first, or a ``{ text | sql | query }`` field of an object."""
    if not args:
        return None
    first = _unwrap(args[0])
    if first is not None and first.type == "object":
        for pair in first.named_children:
            if pair.type != "pair":
                continue
            key = pair.child_by_field_name("key")
            if key is not None and scope.parsed.text(key).strip("'\"") in (
                "text",
                "sql",
                "query",
            ):
                return pair.child_by_field_name("value")
        return None
    return args[0]


def _sql_sink(parts: list[str]) -> tuple[str, bool] | None:
    """``(confidence, needs_sql_text)`` for a raw-SQL call, else None."""
    method = parts[-1] if parts else ""
    if method in _UNSAFE_SQL_METHODS:
        return "high", False
    if len(parts) >= 2 and (parts[-2], method) in _UNSAFE_SQL_CALLS:
        return "high", False
    if method in _SQL_TEXT_METHODS:
        return _SQL_TEXT_METHODS[method], True
    return None


def _sql_issues(
    scope: _Scope, filepath: str, lines: list[str], namer: _Namer
) -> list[dict]:
    issues = []
    for info in q.calls(scope.parsed):
        args_node = info.node.child_by_field_name("arguments")
        if args_node is None or args_node.type != "arguments":
            continue  # a tagged template: its values are parameters
        parts = _callee_parts(scope, info.node)
        sink = _sql_sink(parts)
        if sink is None:
            continue
        confidence, needs_sql_text = sink
        argument = _sql_argument(scope, list(info.arguments))
        built = _interpolated(scope, argument)
        if built is None:
            continue
        if needs_sql_text and not _SQL_STATEMENT_RE.search(_literal_text(scope, built)):
            continue
        sink_name = ".".join(parts[-2:])
        issues.append(
            _entry(
                filepath,
                lines,
                info.line,
                check_id="sql_injection",
                symbol=namer.name(info.node, sink_name),
                summary=f"Raw SQL built from an interpolated value passed to {sink_name}()",
                severity="critical",
                confidence=confidence,
                tier=2,
                remediation=(
                    "Pass values as bound parameters (a tagged sql`` / Prisma.sql template, "
                    "or placeholders plus a values array) instead of building the SQL string"
                ),
            )
        )
    return issues


# ── child_process ───────────────────────────────────────────

_CHILD_PROCESS_MODULES = frozenset({"child_process", "node:child_process"})
_SHELL_FUNCTIONS = frozenset({"exec", "execSync"})
_SPAWN_FUNCTIONS = frozenset({"spawn", "spawnSync", "execFile", "execFileSync"})


@dataclass
class _ProcessBindings:
    shell: dict[str, str] = field(default_factory=dict)  # local name -> exec/execSync
    spawn: dict[str, str] = field(default_factory=dict)  # local name -> spawn family
    modules: set[str] = field(default_factory=set)  # names bound to the module


def _is_child_process_require(scope: _Scope, node) -> bool:
    node = _unwrap(node)
    if node is None or node.type != "call_expression":
        return False
    function = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if function is None or scope.parsed.text(function) != "require" or args is None:
        return False
    values = [a for a in args.named_children if a.type == "string"]
    return (
        bool(values)
        and q.string_value(scope.parsed, values[0]) in _CHILD_PROCESS_MODULES
    )


def _process_bindings(
    scope: _Scope, *, follow_imports: bool = True
) -> _ProcessBindings:
    parsed = scope.parsed
    found = _ProcessBindings()

    def bind(local: str, imported: str) -> None:
        if imported in _SHELL_FUNCTIONS:
            found.shell[local] = imported
        elif imported in _SPAWN_FUNCTIONS:
            found.spawn[local] = imported

    for info in q.imports(parsed):
        if info.type_only:
            continue
        if info.source.startswith(".") and follow_imports:
            for local, imported in _imported_shell_functions(scope, info):
                found.shell[local] = imported
            continue
        if info.source not in _CHILD_PROCESS_MODULES:
            continue
        for binding in info.bindings:
            if binding.imported in ("default", "*", "="):
                found.modules.add(binding.local)
            elif not binding.type_only:
                bind(binding.local, binding.imported)
    for declarators in scope.declarations.values():
        for declarator in declarators:
            value = declarator.child_by_field_name("value")
            name = declarator.child_by_field_name("name")
            if (
                value is None
                or name is None
                or not _is_child_process_require(scope, value)
            ):
                continue
            if name.type == "identifier":
                found.modules.add(parsed.text(name))
    for pattern in q.descendants(parsed.root, ("object_pattern",)):
        declarator = pattern.parent
        if declarator is None or declarator.type != "variable_declarator":
            continue
        if not _is_child_process_require(
            scope, declarator.child_by_field_name("value")
        ):
            continue
        for prop in pattern.named_children:
            if prop.type == "shorthand_property_identifier_pattern":
                bind(parsed.text(prop), parsed.text(prop))
            elif prop.type == "pair_pattern":
                key, value = (
                    prop.child_by_field_name("key"),
                    prop.child_by_field_name("value"),
                )
                if key is not None and value is not None and value.type == "identifier":
                    bind(parsed.text(value), parsed.text(key))
    # ``const run = promisify(exec)`` keeps exec's shell semantics.
    for declarators in scope.declarations.values():
        for declarator in declarators:
            value = _unwrap(declarator.child_by_field_name("value"))
            name = declarator.child_by_field_name("name")
            if (
                value is None
                or name is None
                or name.type != "identifier"
                or value.type != "call_expression"
            ):
                continue
            function = value.child_by_field_name("function")
            args = value.child_by_field_name("arguments")
            if (
                function is None
                or args is None
                or scope.parsed.text(function).split(".")[-1] != "promisify"
            ):
                continue
            target = _process_function(
                scope, found, args.named_children[0] if args.named_children else None
            )
            if target in _SHELL_FUNCTIONS:
                found.shell[parsed.text(name)] = target
    return found


_LOCAL_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def _resolve_local_module(base: Path, source: str) -> Path | None:
    target = (base.parent / source).resolve()
    stem = (
        target.with_suffix("")
        if target.suffix in (".js", ".mjs", ".cjs", ".jsx")
        else target
    )
    for candidate in (
        target,
        *(stem.with_name(stem.name + suffix) for suffix in _LOCAL_SUFFIXES),
        *(target / f"index{suffix}" for suffix in _LOCAL_SUFFIXES),
    ):
        if candidate.is_file():
            return candidate
    return None


def _imported_shell_functions(scope: _Scope, info) -> list[tuple[str, str]]:
    """``(local, exec)`` for each name imported from a local module that exports a
    shell function (``export const run = promisify(exec)``); one hop only."""
    if scope.path is None or not info.bindings:
        return []
    module = _resolve_local_module(scope.path, info.source)
    other = parsed_file(module) if module is not None else None
    if other is None or b"child_process" not in other.source:
        return []
    other_scope = _Scope.build(other, module)
    shell = _process_bindings(other_scope, follow_imports=False).shell
    if not shell:
        return []
    exported: dict[str, str] = {}
    for export in q.exports(other):
        if export.kind in ("declaration", "named"):
            for binding in export.bindings:
                if binding.name in shell:
                    exported[binding.exported] = shell[binding.name]
    return [
        (binding.local, exported[binding.imported])
        for binding in info.bindings
        if not binding.type_only and binding.imported in exported
    ]


def _process_function(scope: _Scope, found: _ProcessBindings, node) -> str | None:
    """The child_process function ``node`` names (``exec``, ``cp.execSync``...), else None."""
    node = _unwrap(node)
    if node is None:
        return None
    if node.type == "identifier":
        name = scope.parsed.text(node)
        return found.shell.get(name) or found.spawn.get(name)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is None or prop is None:
            return None
        method = scope.parsed.text(prop)
        if method not in _SHELL_FUNCTIONS | _SPAWN_FUNCTIONS:
            return None
        if (
            obj.type == "identifier" and scope.parsed.text(obj) in found.modules
        ) or _is_child_process_require(scope, obj):
            return method
    return None


def _shell_option(scope: _Scope, args: list) -> bool:
    """A ``{ shell: true }`` (or a shell path) options argument."""
    for arg in args[1:]:
        arg = _unwrap(arg)
        if arg is None or arg.type != "object":
            continue
        for pair in arg.named_children:
            if pair.type != "pair":
                continue
            key, value = (
                pair.child_by_field_name("key"),
                pair.child_by_field_name("value"),
            )
            if (
                key is not None
                and scope.parsed.text(key) == "shell"
                and value is not None
            ):
                if value.type == "true" or value.type == "string":
                    return True
    return False


def _shell_issues(
    scope: _Scope, filepath: str, lines: list[str], namer: _Namer
) -> list[dict]:
    found = _process_bindings(scope)
    if not (found.shell or found.spawn or found.modules):
        return []
    issues = []
    for info in q.calls(scope.parsed):
        function = info.node.child_by_field_name("function")
        target = _process_function(scope, found, function)
        if target is None:
            continue
        args = list(info.arguments)
        if target in _SPAWN_FUNCTIONS and not _shell_option(scope, args):
            continue
        built = _interpolated(scope, args[0] if args else None)
        if built is None:
            continue
        callee = scope.parsed.text(function) if function is not None else target
        issues.append(
            _entry(
                filepath,
                lines,
                info.line,
                check_id="shell_injection",
                symbol=namer.name(info.node, callee),
                summary=f"Shell command built from an interpolated value passed to {callee}()",
                severity="critical",
                confidence="high",
                tier=2,
                remediation=(
                    "Run the program with execFile/spawn and an argument array (no shell), "
                    "or validate the value against an allowlist"
                ),
            )
        )
    return issues


# ── Server actions and route handlers ───────────────────────

_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_ROUTE_FILE_RE = re.compile(r"(?:^|/)app/(?:.*/)?route\.(?:[mc]?[jt]sx?)$")
DEFAULT_AUTH_FUNCTIONS = (
    "auth",
    "getAuth",
    "currentUser",
    "getCurrentUser",
    "getUser",
    "getSession",
    "getServerSession",
    "getServerAuthSession",
    "getIronSession",
    "getKindeServerSession",
    "getToken",
    "withAuth",
    "withApiAuthRequired",
    "withPageAuthRequired",
    "authenticate",
    "authorize",
    "validateRequest",
    "verifyToken",
    "verifyIdToken",
    "verifySessionCookie",
    "jwtVerify",
    "constructEvent",
    "verifySignature",
    "verifyWebhook",
)
_AUTH_NAME_RE = re.compile(
    r"^(?:require|ensure|check|verify|validate|assert|authorize|authenticate|protect)\w*?"
    r"(?:Auth|Session|User|Admin|Role|Permission|Access|Token|Signature|Webhook|Login|Logged|Signed|Owner|Member)"
    r"|^(?:is|has)(?:Authenticated|Authori[sz]ed|LoggedIn|SignedIn|Admin|Permission|Role|Access)",
)
# A shared secret or signed header compared in the handler (webhooks, cron, revalidation).
_SECRET_CHECK_RE = re.compile(
    r"process\.env(?:\.|\[['\"])\w*(?:SECRET|TOKEN|API_?KEY|PASSWORD|SIGNATURE)"
    r"|['\"`](?:authorization|x-api-key|[\w-]*signature[\w-]*|[\w-]*-secret|x-[\w-]*-token)['\"`]",
    re.IGNORECASE,
)
# Dependencies that give an app user accounts. Without one (or a configured auth
# function) an app has no notion of a signed-in user, so its actions are public on purpose.
_AUTH_PACKAGES = frozenset(
    {
        "next-auth",
        "lucia",
        "better-auth",
        "iron-session",
        "jsonwebtoken",
        "passport",
        "firebase-admin",
        "next-firebase-auth-edge",
        "@supabase/ssr",
        "@supabase/auth-helpers-nextjs",
        "@kinde-oss/kinde-auth-nextjs",
        "@workos-inc/authkit-nextjs",
        "@auth0/nextjs-auth0",
        "@stackframe/stack",
        "@propelauth/nextjs",
        "@descope/nextjs-sdk",
        "@logto/next",
        "arctic",
    }
)
_AUTH_PACKAGE_RE = re.compile(r"^@(?:auth|clerk)/|(?:^|[/-])(?:auth|authjs)$")
_MIDDLEWARE_NAMES = ("middleware", "proxy")


@dataclass
class _Candidate:
    kind: str  # server_action / route_handler
    name: str
    function: object
    wrapper: object | None  # the wrapping call, if any
    line: int


def _directives(parsed: ParsedSource, block) -> set[str]:
    found = set()
    for statement in q.statements(block):
        value = q.directive(parsed, statement)
        if value is None:
            break
        found.add(value)
    return found


def _resolve_export_value(
    scope: _Scope, value, depth: int = 0
) -> tuple[object | None, object | None] | None:
    """``(function, wrapper_call)`` behind an exported value, or None when it
    isn't a function written here (a re-export, an import). A builder chain
    ending in a callback (``procedure.mutation(async () => …)``) is a wrapper call."""
    value = _unwrap(value)
    if value is None or depth > 2:
        return None
    if value.type in _FUNCTION_TYPES:
        return value, None
    if value.type == "identifier":
        target = scope.top_functions.get(scope.parsed.text(value))
        if target is not None:
            return target, None
        init = scope.initializer(scope.parsed.text(value), value)
        return (
            _resolve_export_value(scope, init, depth + 1) if init is not None else None
        )
    if value.type == "call_expression":
        args = value.child_by_field_name("arguments")
        inner = [
            a
            for a in (args.named_children if args is not None else ())
            if _function_value(a) is not None
        ]
        if len(inner) == 1:
            return _function_value(inner[0]), value
    return None


def _candidates(scope: _Scope, normalized_path: str) -> list[_Candidate]:
    parsed = scope.parsed
    module_server = "use server" in _directives(parsed, parsed.root)
    is_route = bool(_ROUTE_FILE_RE.search(normalized_path))
    if not (module_server or is_route or b"use server" in parsed.source):
        return []
    found: list[_Candidate] = []

    def consider(exported: str, value, line: int) -> None:
        resolved = _resolve_export_value(scope, value)
        if resolved is None or resolved[0] is None:
            return
        function, wrapper = resolved
        body = function.child_by_field_name("body")
        inline_server = (
            body is not None
            and body.type == "statement_block"
            and "use server" in _directives(parsed, body)
        )
        if is_route and not module_server:
            if exported in _MUTATING_METHODS:
                found.append(
                    _Candidate("route_handler", exported, function, wrapper, line)
                )
        elif module_server or inline_server:
            found.append(_Candidate("server_action", exported, function, wrapper, line))

    for info in q.exports(parsed):
        if info.kind == "reexport" or info.type_only:
            continue
        line = info.line
        if info.kind == "declaration":
            declaration = info.node.child_by_field_name("declaration")
            if declaration is None:
                continue
            if declaration.type == "function_declaration":
                name = declaration.child_by_field_name("name")
                consider(
                    "default" if info.is_default else parsed.text(name),
                    declaration,
                    line,
                )
            elif declaration.type in ("lexical_declaration", "variable_declaration"):
                for declarator in declaration.named_children:
                    name = declarator.child_by_field_name("name")
                    if name is not None and name.type == "identifier":
                        consider(
                            parsed.text(name),
                            declarator.child_by_field_name("value"),
                            line,
                        )
        elif info.kind == "named":
            for binding in info.bindings:
                if binding.type_only or binding.name is None:
                    continue
                target = scope.top_functions.get(binding.name)
                if target is not None:
                    consider(binding.exported, target, line)
        elif info.kind == "default":
            value = info.node.child_by_field_name("value")
            if value is not None:
                consider("default", value, line)
    return found


def _is_auth_package(name: str) -> bool:
    return name in _AUTH_PACKAGES or bool(_AUTH_PACKAGE_RE.search(name))


def _is_auth_name(name: str, auth_names: frozenset[str]) -> bool:
    return name.lower() in auth_names or bool(_AUTH_NAME_RE.match(name))


def _has_auth(
    scope: _Scope, node, auth_names: frozenset[str], seen: set[int], depth: int = 0
) -> bool:
    """A call to an auth/session function, or a secret check, anywhere in ``node``;
    same-file helpers it calls are followed."""
    if node is None or depth > 3 or node.id in seen:
        return False
    seen.add(node.id)
    if _SECRET_CHECK_RE.search(scope.parsed.text(node)):
        return True
    for call in q.descendants(node, ("call_expression",)):
        parts = _callee_parts(scope, call)
        if any(
            _is_auth_name(re.sub(r"\(.*", "", part), auth_names)
            for part in parts
            if part
        ):
            return True
        if len(parts) == 1:
            helper = scope.top_functions.get(parts[0])
            if helper is not None and _has_auth(
                scope, helper, auth_names, seen, depth + 1
            ):
                return True
    return False


def _is_name(scope: _Scope, node, name: str) -> bool:
    node = _unwrap(node)
    return (
        node is not None
        and node.type == "identifier"
        and scope.parsed.text(node) == name
    )


def _delegates_request(scope: _Scope, function) -> bool:
    """A handler that hands its request to another function, which may check it."""
    params = function.child_by_field_name("parameters")
    first = (
        params.named_children[0]
        if params is not None and params.named_children
        else None
    )
    pattern = first.child_by_field_name("pattern") if first is not None else None
    if pattern is None or pattern.type != "identifier":
        return False
    request = scope.parsed.text(pattern)
    for call in q.descendants(function, ("call_expression",)):
        args = call.child_by_field_name("arguments")
        if args is None or args.type != "arguments":
            continue
        for arg in args.named_children:
            arg = _unwrap(arg)
            if arg is None:
                continue
            if _is_name(scope, arg, request):
                return True
            if arg.type == "object" and any(
                (
                    p.type == "shorthand_property_identifier"
                    and scope.parsed.text(p) == request
                )
                or (
                    p.type == "pair"
                    and _is_name(scope, p.child_by_field_name("value"), request)
                )
                for p in arg.named_children
            ):
                return True
    return False


def _wrapper_status(
    scope: _Scope, wrapper, auth_names: frozenset[str], seen: set[int]
) -> str:
    """``auth`` when the wrapper checks auth, ``unknown`` when it can't be seen, else ``none``."""
    parts = _callee_parts(scope, wrapper)
    if any(_is_auth_name(part, auth_names) for part in parts):
        return "auth"
    local = scope.top_functions.get(parts[0]) if len(parts) == 1 else None
    if local is None:
        return "unknown"
    return "auth" if _has_auth(scope, local, auth_names, seen) else "none"


class _AppAuthContext:
    """Per-app facts, cached by directory: does the app have user accounts,
    and does a middleware file check auth for it."""

    def __init__(self, auth_names: frozenset[str], configured: bool) -> None:
        self.auth_names = auth_names
        self.configured = configured
        self._apps: dict[Path, tuple[bool, bool]] = {}

    def for_file(self, filepath: str) -> tuple[bool, bool]:
        start = Path(resolve_path(filepath)).parent
        for directory in (start, *start.parents):
            manifest = directory / "package.json"
            if manifest.is_file():
                if directory not in self._apps:
                    self._apps[directory] = self._inspect(directory, manifest)
                return self._apps[directory]
        return self.configured, False

    def _inspect(self, directory: Path, manifest: Path) -> tuple[bool, bool]:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = {}
        names: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = data.get(key) if isinstance(data, dict) else None
            if isinstance(section, dict):
                names.update(str(name) for name in section)
        has_accounts = self.configured or any(_is_auth_package(name) for name in names)
        return has_accounts, self._middleware_checks_auth(directory)

    def _middleware_checks_auth(self, directory: Path) -> bool:
        for base in (directory, directory / "src"):
            for stem in _MIDDLEWARE_NAMES:
                for suffix in (".ts", ".js", ".mts", ".mjs", ".tsx", ".jsx"):
                    path = base / f"{stem}{suffix}"
                    if not path.is_file():
                        continue
                    parsed = parsed_file(path)
                    if parsed is None:
                        try:
                            parsed = parse_text(
                                path.read_text(encoding="utf-8", errors="replace"), path
                            )
                        except OSError:
                            parsed = None
                    if parsed is None:
                        continue
                    scope = _Scope.build(parsed)
                    if any(_is_auth_package(src) for src in scope.imported.values()):
                        return True
                    reexported = (
                        b.name or "" for e in q.exports(parsed) for b in e.bindings
                    )
                    if any(_is_auth_name(name, self.auth_names) for name in reexported):
                        return True
                    if _has_auth(scope, parsed.root, self.auth_names, set()):
                        return True
        return False


def _auth_issues(
    scope: _Scope,
    filepath: str,
    normalized_path: str,
    lines: list[str],
    app: _AppAuthContext,
) -> list[dict]:
    candidates = _candidates(scope, normalized_path)
    if not candidates:
        return []
    has_accounts, middleware_auth = app.for_file(filepath)
    if not has_accounts:
        return []
    issues = []
    for candidate in candidates:
        seen: set[int] = set()
        if (
            candidate.wrapper is not None
            and _wrapper_status(scope, candidate.wrapper, app.auth_names, seen)
            != "none"
        ):
            continue
        if _has_auth(scope, candidate.function, app.auth_names, seen):
            continue
        if candidate.kind == "route_handler" and _delegates_request(
            scope, candidate.function
        ):
            continue
        confidence = "low" if middleware_auth else "medium"
        if candidate.kind == "server_action":
            check_id = "server_action_missing_auth"
            summary = f"Server action {candidate.name}() runs without an auth or session check"
            remediation = (
                "Server actions are public POST endpoints: check the session (auth(), getServerSession(), "
                "currentUser()...) at the top of the action, or add your check's name to "
                "languages.typescript.auth_functions"
            )
        else:
            check_id = "route_handler_missing_auth"
            summary = f"Route handler {candidate.name} changes state without an auth, session or secret check"
            remediation = (
                "Check the session or a shared secret before acting, or add your check's name to "
                "languages.typescript.auth_functions"
            )
        issues.append(
            _entry(
                filepath,
                lines,
                candidate.line,
                check_id=check_id,
                symbol=candidate.name,
                summary=summary,
                severity="high",
                confidence=confidence,
                tier=3,
                remediation=remediation,
            )
        )
    return issues


# ── Entries ─────────────────────────────────────────────────


class _Namer:
    """Stable symbols for call findings: ``<enclosing function>:<callee>``, with
    ``#n`` for the n-th such call in the same function."""

    def __init__(self, parsed: ParsedSource) -> None:
        self.parsed = parsed
        self._counts: dict[str, int] = {}

    def name(self, call, callee: str) -> str:
        owner = "<module>"
        holder = _enclosing_function(call)
        while holder is not None:
            info = q.function_info(self.parsed, holder)
            if info is not None and info.name:
                owner = f"{info.owner}.{info.name}" if info.owner else info.name
                break
            holder = _enclosing_function(holder)
        base = f"{owner}:{callee}"
        self._counts[base] = self._counts.get(base, 0) + 1
        count = self._counts[base]
        return base if count == 1 else f"{base}#{count}"


def _entry(
    filepath: str,
    lines: list[str],
    line: int,
    *,
    check_id: str,
    symbol: str,
    summary: str,
    severity: str,
    confidence: str,
    tier: int,
    remediation: str,
) -> dict:
    entry = _make_security_entry(
        filepath,
        line,
        lines[line - 1] if 0 < line <= len(lines) else "",
        check_id=check_id,
        summary=summary,
        severity=severity,
        confidence=confidence,
        remediation=remediation,
    )
    entry["name"] = f"{check_id}::{symbol}"
    entry["tier"] = tier
    entry["detail"]["symbol"] = symbol
    return entry


def auth_function_names(
    configured: Iterable[str] | None,
) -> tuple[frozenset[str], bool]:
    """The auth-function names to recognise (lower-cased) and whether any were configured."""
    extra = [str(name).strip() for name in (configured or ()) if str(name).strip()]
    return frozenset(name.lower() for name in (*DEFAULT_AUTH_FUNCTIONS, *extra)), bool(
        extra
    )


def make_app_context(settings: Mapping[str, object] | None) -> _AppAuthContext:
    configured = (settings or {}).get("auth_functions")
    names, has_configured = auth_function_names(
        configured if isinstance(configured, list) else None
    )
    return _AppAuthContext(names, has_configured)


def backend_security_issues(
    *,
    filepath: str,
    normalized_path: str,
    lines: list[str],
    app: _AppAuthContext,
) -> list[dict]:
    """SQL, shell and missing-auth findings for one file; none without tree-sitter."""
    parsed = parsed_file(filepath)
    if parsed is None:
        return []
    scope = _Scope.build(parsed, Path(resolve_path(filepath)))
    namer = _Namer(parsed)
    return [
        *_sql_issues(scope, filepath, lines, namer),
        *_shell_issues(scope, filepath, lines, namer),
        *_auth_issues(scope, filepath, normalized_path, lines, app),
    ]


__all__ = [
    "DEFAULT_AUTH_FUNCTIONS",
    "auth_function_names",
    "backend_security_issues",
    "make_app_context",
]
