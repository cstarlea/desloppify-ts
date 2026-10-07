"""Tree-sitter parser loading and query helpers."""

from __future__ import annotations

PARSE_INIT_ERRORS: tuple[type[Exception], ...] = (
    ImportError,
    OSError,
    ValueError,
    RuntimeError,
)


def _make_query(language, source: str):
    """Create a tree-sitter Query."""
    from tree_sitter import Query
    return Query(language, source)


def _run_query(query, root_node) -> list[tuple[int, dict]]:
    """Run a query and return matches."""
    from tree_sitter import QueryCursor
    cursor = QueryCursor(query)
    return cursor.matches(root_node)


# Grammars that failed to load this process, e.g. because the language pack
# could not download them offline. Phases swallow these errors, so the scan
# reports them as reduced coverage instead (see record_grammar_load_failures).
_GRAMMAR_FAILURES: dict[str, str] = {}


def _get_parser(grammar: str):
    """Get a tree-sitter parser and language for the given grammar."""
    from tree_sitter_language_pack import get_language, get_parser

    try:
        parser = get_parser(grammar)
        language = get_language(grammar)
    except Exception as exc:
        _GRAMMAR_FAILURES.setdefault(grammar, f"{type(exc).__name__}: {exc}")
        raise
    return parser, language


def grammar_load_failures() -> dict[str, str]:
    """Return grammars that failed to load since the last reset."""
    return dict(_GRAMMAR_FAILURES)


def reset_grammar_load_failures() -> None:
    _GRAMMAR_FAILURES.clear()


def _unwrap_node(node):
    """Unwrap a capture that may be a list of nodes."""
    if isinstance(node, list):
        return node[0] if node else None
    return node


def _node_text(node) -> str:
    """Get text from a node as a str."""
    text = node.text
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return str(text)


__all__ = [
    "PARSE_INIT_ERRORS",
    "grammar_load_failures",
    "reset_grammar_load_failures",
]
