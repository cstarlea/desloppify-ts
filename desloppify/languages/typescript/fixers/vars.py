"""Unused-vars fixer that edits syntax-tree ranges.

Each tsc finding is matched to the declaring name at its reported line and
column (or, failing that, the only name with that text on the line). The
fixer removes:

- a declarator (``const a = 1``, or ``b`` in ``let a = 1, b = 2``) whose
  initializer has no side effects, and the statement once it's empty;
- a member of an object destructuring pattern (``{ a, b: c, d = 1 }``);
- the member or element holding a nested pattern that ends up empty
  (``a: { b }``), outward as far as the declarator;
- a local function, type alias or interface, with a JSDoc block directly above.

It skips what it can't remove without changing behaviour: initializers or
defaults that may run code, patterns with ``...rest`` (whose contents would
change), declarators whose pattern would end up empty but whose initializer
may run code, array elements unless the whole array pattern goes, and names that
appear anywhere else in their scope (tsc reports variables that are written
but never read, and removing the declaration would leave those writes
dangling). Parameters are left to the unused-params fixer.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.syntax.nodes import (
    ALL_DESTRUCTURED,
    FUNCTIONS,
    STATEMENT_PARENTS,
    NameIndex,
    asi_hazards,
    binding_names,
    byte_offset,
    is_parameter,
    node_key,
    same,
    within,
)
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .edits import apply_edits, comma_list_edits, whole_statement_range
from .fixer_io import apply_fixer

ALL_VARIABLES = "(all variables)"

_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
_DECLARATION_KINDS = frozenset({"const", "let", "var"})
_NAMED_STATEMENTS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "type_alias_declaration",
        "interface_declaration",
    }
)
_LOOPS = frozenset({"for_statement", "for_in_statement"})
_BLOCKS = frozenset({"program", "statement_block", "switch_body", *_LOOPS, *FUNCTIONS})
_PATTERN_WRAPPERS = frozenset(
    {
        "pair_pattern",
        "object_assignment_pattern",
        "assignment_pattern",
        "object_pattern",
        "array_pattern",
    }
)
_PURE_LEAVES = frozenset(
    {
        "string",
        "number",
        "true",
        "false",
        "null",
        "undefined",
        "identifier",
        "regex",
        "this",
        "arrow_function",
        "function_expression",
        "function",
        "generator_function",
    }
)
_PURE_WRAPPERS = frozenset(
    {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
        "non_null_expression",
    }
)


@dataclass
class _Target:
    node: object
    entries: list[dict] = field(default_factory=list)


def fix_unused_vars(entries: list[dict], *, dry_run: bool = False) -> FixResult:
    """Remove unused declarations reported by the ``unused`` detector."""
    if entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the unused-vars fixer needs tree-sitter (install desloppify-ts[full]).",
                "yellow",
            ),
            file=sys.stderr,
        )
        return FixResult(entries=[], skip_reasons={"needs_treesitter": len(entries)})

    skip_reasons: dict[str, int] = defaultdict(int)

    def transform(
        lines: list[str], file_entries: list[dict]
    ) -> tuple[list[str], list[dict]]:
        path = str(file_entries[0].get("file", "")) if file_entries else ""
        parsed = parse_text("".join(lines), path)
        if parsed is None:
            return lines, []
        new_source, fixed, skipped = remove_unused_vars(parsed, file_entries)
        for reason in skipped:
            skip_reasons[reason] += 1
        if not fixed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), fixed

    results = apply_fixer(entries, transform, dry_run=dry_run)
    return FixResult(entries=results, skip_reasons=dict(skip_reasons))


def remove_unused_vars(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[dict], list[str]]:
    """Return the edited source, the fixed entries and a skip reason per skipped entry."""
    planner = _Planner(parsed)
    for entry in file_entries:
        reason = planner.plan(entry)
        if reason is not None:
            planner.skipped.append(reason)
    edits = planner.resolve()
    return apply_edits(parsed.source, edits), planner.fixed, planner.skipped


class _Planner:
    """Maps entries to nodes to delete, then turns them into byte ranges."""

    def __init__(self, parsed: ParsedSource) -> None:
        self.parsed = parsed
        self.fixed: list[dict] = []
        self.skipped: list[str] = []
        # Removals grouped by the comma list they come out of, or whole statements.
        self._patterns: dict[tuple, tuple[object, dict[tuple, _Target]]] = {}
        self._declarations: dict[tuple, tuple[object, dict[tuple, _Target]]] = {}
        self._statements: dict[tuple, _Target] = {}
        self._names = NameIndex(parsed)

    # -- planning ---------------------------------------------------------

    def plan(self, entry: dict) -> str | None:
        name, line, col = entry.get("name"), entry.get("line"), entry.get("col")
        if not isinstance(name, str) or not isinstance(line, int):
            return "not_found"
        if name in (ALL_DESTRUCTURED, ALL_VARIABLES):
            return self._plan_aggregate(entry, name, line, col)

        node = self._names.find(name, line, col)
        if node is None:
            return "not_found"
        parent = node.parent
        if is_parameter(node):
            return "function_param"
        if parent.type in _NAMED_STATEMENTS and same(
            parent.child_by_field_name("name"), node
        ):
            return self._plan_statement(entry, parent, [node])
        if node.type == "type_identifier":
            return "type_parameter" if parent.type == "type_parameter" else "other"
        if parent.type == "variable_declarator" and same(
            parent.child_by_field_name("name"), node
        ):
            return self._plan_declarator(entry, parent, [node])
        if parent.type == "rest_pattern":
            return "rest_element"
        return self._plan_member(entry, node)

    def _plan_aggregate(
        self, entry: dict, name: str, line: int, col: object
    ) -> str | None:
        offset = (
            byte_offset(self.parsed.source, line, col) if isinstance(col, int) else None
        )
        if offset is None:
            return "not_found"
        wanted = (
            ("object_pattern", "array_pattern")
            if name == ALL_DESTRUCTURED
            else _DECLARATIONS
        )
        node = self.parsed.root.named_descendant_for_byte_range(offset, offset)
        while (
            node is not None and node.type not in wanted and node.start_byte == offset
        ):
            node = node.parent
        if node is None or node.type not in wanted or node.start_byte != offset:
            return "not_found"
        if name == ALL_VARIABLES:
            statement = _declaration_statement(node)
            if isinstance(statement, str):
                return statement
            declarators = [
                c for c in node.named_children if c.type == "variable_declarator"
            ]
            if statement.type != "ambient_declaration" and not all(
                _is_pure(d.child_by_field_name("value")) for d in declarators
            ):
                return "side_effects"
            names = [
                n
                for d in declarators
                for n in binding_names(d.child_by_field_name("name"))
            ]
            return self._plan_statement(entry, statement, names)
        if is_parameter(node):
            return "function_param"
        declarator = node.parent
        if declarator.type == "variable_declarator" and same(
            declarator.child_by_field_name("name"), node
        ):
            return self._plan_declarator(entry, declarator, binding_names(node))
        # tsc reports a nested pattern, like `{ b, c }` in `{ a: { b, c } }`, on its own.
        items = _list_items(node)
        if _pattern_member(node)[0] is None or not items:
            return "other"
        return self._plan_in_pattern(entry, node, items, binding_names(node), node)

    def _plan_statement(self, entry: dict, statement, names: list) -> str | None:
        if statement.parent is None or statement.parent.type not in STATEMENT_PARENTS:
            return "other"
        function_scoped = statement.type != "lexical_declaration"
        if self._used_elsewhere(
            names, statement, statement, function_scoped=function_scoped
        ):
            return "written_elsewhere"
        self._statements.setdefault(
            node_key(statement), _Target(statement)
        ).entries.append(entry)
        return None

    def _plan_declarator(self, entry: dict, declarator, names: list) -> str | None:
        declaration = declarator.parent
        reason = _declarator_blocker(declarator)
        if reason is not None:
            return reason
        function_scoped = _kind(declaration) == "var"
        if self._used_elsewhere(
            names, declaration, declarator, function_scoped=function_scoped
        ):
            return "written_elsewhere"
        self._add(self._declarations, declaration, declarator, [entry])
        return None

    def _plan_member(self, entry: dict, node) -> str | None:
        member, default = _pattern_member(node)
        if member is None:
            return "other"
        reason = _member_blocker(member, default)
        if reason is not None:
            return reason
        return self._plan_in_pattern(entry, member.parent, [member], [node], member)

    def _plan_in_pattern(
        self, entry: dict, pattern, items: list, names: list, removed
    ) -> str | None:
        """Plan removing ``items`` (within ``removed``, binding ``names``) from ``pattern``."""
        top = pattern
        while top.parent is not None and top.parent.type in _PATTERN_WRAPPERS:
            top = top.parent
        declarator = top.parent
        if (
            declarator is None
            or declarator.type != "variable_declarator"
            or not same(declarator.child_by_field_name("name"), top)
        ):
            return "other"
        statement = _declaration_statement(declarator.parent)
        if isinstance(statement, str):
            return statement
        function_scoped = _kind(declarator.parent) == "var"
        if self._used_elsewhere(
            names, declarator.parent, removed, function_scoped=function_scoped
        ):
            return "written_elsewhere"
        for item in items:
            self._add(self._patterns, pattern, item, [])
        self._add(self._patterns, pattern, items[0], [entry])
        return None

    @staticmethod
    def _add(groups: dict, container, item, entries: list[dict]) -> None:
        _container, items = groups.setdefault(node_key(container), (container, {}))
        items.setdefault(node_key(item), _Target(item)).entries.extend(entries)

    # -- lookups ----------------------------------------------------------

    def _used_elsewhere(
        self, names: list, declaration, removed, *, function_scoped: bool
    ) -> bool:
        """Whether any of ``names`` occurs in the scope ``declaration`` declares into.

        Occurrences inside ``removed`` (the node being deleted, which holds
        the declaration itself and any self-references) don't count.
        """
        scope = _scope(declaration, function_scoped=function_scoped)
        for name_node in names:
            for other in self._names.get(self.parsed.text(name_node)):
                if within(other, removed):
                    continue
                if within(other, scope):
                    return True
        return False

    # -- resolving --------------------------------------------------------

    def resolve(self) -> list[tuple[int, int]]:
        edits: list[tuple[int, int]] = []
        # Innermost patterns first: one that empties goes from the pattern around it.
        while self._patterns:
            key = max(self._patterns, key=lambda k: _depth(self._patterns[k][0]))
            pattern, members = self._patterns.pop(key)
            items = _list_items(pattern)
            remove = {i for i, item in enumerate(items) if node_key(item) in members}
            entries = [e for target in members.values() for e in target.entries]
            if len(remove) < len(items):
                if pattern.type == "array_pattern":
                    self.skipped.extend(["array_destructuring"] * len(entries))
                else:
                    edits.extend(comma_list_edits(items, remove))
                    self.fixed.extend(entries)
                continue
            reason = self._remove_emptied(pattern, entries)
            if reason is not None:
                self.skipped.extend([reason] * len(entries))

        for declaration, declarators in self._declarations.values():
            items = _list_items(declaration)
            remove = {
                i for i, item in enumerate(items) if node_key(item) in declarators
            }
            entries = [e for target in declarators.values() for e in target.entries]
            if len(remove) < len(items):
                edits.extend(comma_list_edits(items, remove))
                self.fixed.extend(entries)
            else:
                statement = _declaration_statement(declaration)
                target = self._statements.setdefault(
                    node_key(statement), _Target(statement)
                )
                target.entries.extend(entries)

        source = self.parsed.source
        removing = dict(self._statements)
        for key in asi_hazards(source, {k: t.node for k, t in removing.items()}):
            self.skipped.extend(["asi_hazard"] * len(removing.pop(key).entries))
        for target in removing.values():
            edits.append(whole_statement_range(source, target.node, leading_jsdoc=True))
            self.fixed.extend(target.entries)
        return edits

    def _remove_emptied(self, pattern, entries: list[dict]) -> str | None:
        """Remove ``pattern``'s declarator or enclosing member, or say why not."""
        declarator = pattern.parent
        if declarator.type == "variable_declarator" and same(
            declarator.child_by_field_name("name"), pattern
        ):
            if _declarator_blocker(declarator) is not None:
                return "would_empty_pattern"
            self._add(self._declarations, declarator.parent, declarator, entries)
            return None
        member, default = _pattern_member(pattern)
        if member is None:
            return "would_empty_pattern"
        reason = _member_blocker(member, default)
        if reason is not None:
            return reason
        self._add(self._patterns, member.parent, member, entries)
        return None


def _declarator_blocker(declarator) -> str | None:
    """Why removing the whole declarator could change behaviour, or None."""
    statement = _declaration_statement(declarator.parent)
    if isinstance(statement, str):
        return statement
    if statement.type != "ambient_declaration" and not _is_pure(
        declarator.child_by_field_name("value")
    ):
        return "side_effects"
    return None


def _kind(declaration) -> str:
    first = declaration.child(0)
    return first.type if first is not None else ""


def _declaration_statement(declaration):
    """The statement a declaration stands for, or why it can't be removed."""
    if declaration is None or declaration.type not in _DECLARATIONS:
        return "other"
    if _kind(declaration) not in _DECLARATION_KINDS:
        return "other"  # `using`/`await using` run disposers
    statement = declaration
    if statement.parent is not None and statement.parent.type == "ambient_declaration":
        statement = statement.parent
    if statement.parent is not None and statement.parent.type in _LOOPS:
        return "loop_variable"
    if statement.parent is None or statement.parent.type not in STATEMENT_PARENTS:
        return "other"
    return statement


def _list_items(container) -> list:
    if container.type in _DECLARATIONS:
        return [c for c in container.named_children if c.type == "variable_declarator"]
    return [c for c in container.named_children if c.type != "comment"]


def _pattern_member(node):
    """The pattern member that binds ``node`` (a name or nested pattern), and its default."""
    parent = node.parent
    if node.type == "shorthand_property_identifier_pattern":
        if parent.type == "object_pattern":
            return node, None
        if (
            parent.type == "object_assignment_pattern"
            and parent.parent.type == "object_pattern"
        ):
            return parent, parent.child_by_field_name("right")
        return None, None
    if node.type not in ("identifier", "object_pattern", "array_pattern"):
        return None, None
    default = None
    if parent.type == "assignment_pattern" and same(
        parent.child_by_field_name("left"), node
    ):
        node, default, parent = (
            parent,
            parent.child_by_field_name("right"),
            parent.parent,
        )
    if parent.type == "pair_pattern" and same(
        parent.child_by_field_name("value"), node
    ):
        return parent, default
    if parent.type == "array_pattern":
        return node, default
    return None, None


def _member_blocker(member, default) -> str | None:
    """Why removing ``member`` from its pattern could change behaviour, or None."""
    pattern = member.parent
    if pattern.type == "object_pattern" and any(
        c.type == "rest_pattern" for c in pattern.named_children
    ):
        return "rest_element"
    key = member.child_by_field_name("key") if member.type == "pair_pattern" else None
    if not _is_pure(default) or (
        key is not None and key.type == "computed_property_name"
    ):
        return "side_effects"
    return None


def _depth(node) -> int:
    depth = 0
    while node.parent is not None:
        node, depth = node.parent, depth + 1
    return depth


def _scope(node, *, function_scoped: bool):
    parent = node.parent
    while parent is not None:
        if parent.type == "program" or parent.type in (
            FUNCTIONS if function_scoped else _BLOCKS
        ):
            return parent
        parent = parent.parent
    return node


def _is_pure(node) -> bool:
    """Whether evaluating ``node`` can't run user code or throw (None is pure)."""
    if node is None:
        return True
    kind = node.type
    if kind in _PURE_LEAVES:
        return True
    if kind == "template_string":
        return not any(c.type == "template_substitution" for c in node.named_children)
    if kind in _PURE_WRAPPERS:
        return bool(node.named_children) and _is_pure(node.named_children[0])
    if kind == "type_assertion":
        return bool(node.named_children) and _is_pure(node.named_children[-1])
    if kind == "unary_expression":
        operator = node.child_by_field_name("operator")
        argument = node.child_by_field_name("argument")
        if operator is None:
            return False
        if operator.type in ("!", "typeof", "void"):
            return _is_pure(argument)
        return (
            operator.type in ("-", "+")
            and argument is not None
            and argument.type == "number"
        )
    if kind == "array":
        return all(
            c.type != "spread_element" and _is_pure(c)
            for c in node.named_children
            if c.type != "comment"
        )
    if kind == "object":
        for child in node.named_children:
            if child.type in (
                "comment",
                "shorthand_property_identifier",
                "method_definition",
            ):
                continue
            if child.type != "pair":
                return False  # spread
            key = child.child_by_field_name("key")
            if key is None or key.type == "computed_property_name":
                return False
            if not _is_pure(child.child_by_field_name("value")):
                return False
        return True
    return False


__all__ = ["ALL_DESTRUCTURED", "ALL_VARIABLES", "fix_unused_vars", "remove_unused_vars"]
