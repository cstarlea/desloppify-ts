"""JavaScript/TypeScript-oriented text helpers.

These helpers are intentionally framework-agnostic and live under the shared
Node layer so they can be used by framework scanners across JS/TS plugins.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence

from desloppify.base.text_utils import strip_c_style_comments


def strip_js_ts_comments(text: str) -> str:
    """Strip // and /* */ comments while preserving string literals."""
    return strip_c_style_comments(text)


def scan_code(text: str) -> Generator[tuple[int, str, bool], None, None]:
    """Yield ``(index, char, in_string)`` tuples while handling escapes."""
    i = 0
    in_str = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < len(text):
                yield (i, ch, True)
                i += 1
                yield (i, text[i], True)
                i += 1
                continue
            if ch == in_str:
                in_str = None
            yield (i, ch, in_str is not None)
        else:
            if ch in ("'", '"', "`"):
                in_str = ch
                yield (i, ch, True)
            else:
                yield (i, ch, False)
        i += 1


# A ``/`` after one of these (or at the start) begins a regex literal, not a division.
_REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%>~^")
_REGEX_AFTER_WORDS = frozenset(
    {
        "return",
        "typeof",
        "case",
        "do",
        "else",
        "in",
        "of",
        "void",
        "yield",
        "await",
        "delete",
        "throw",
        "new",
    }
)


def code_text(text: str, jsx_text: Sequence[tuple[int, int]] = ()) -> str:
    """Blank comments and string, template and regex literals to spaces.

    Positions and newlines are kept, and so is the code inside a template's
    ``${...}``. A quote or regex not closed on its own line ends at the line
    break, so a stray apostrophe hides at most that line. ``jsx_text`` gives
    the JSX text spans (see ``literal_spans``).
    """
    return blank_spans(text, literal_spans(text, jsx_text))


def blank_spans(text: str, spans) -> str:
    """``text`` with each ``(start, end, kind)`` span blanked to spaces, newlines kept."""
    out = list(text)
    for start, end, _kind in spans:
        for k in range(start, end):
            if text[k] != "\n":
                out[k] = " "
    return "".join(out)


def literal_spans(
    text: str, jsx_text: Sequence[tuple[int, int]] = ()
) -> Generator[tuple[int, int, str], None, None]:
    """``(start, end, kind)`` for each comment and literal, in order (see ``code_text``).

    ``kind`` is ``comment``, ``string``, ``template``, ``regex`` or ``jsx``. A
    template with substitutions gives one span per piece of text around its
    ``${...}``, each piece's delimiters included. ``jsx_text`` is the sorted
    ``(start, end)`` offsets of JSX text, which the lexer can't tell from code
    on its own: each one reached in code is a ``jsx`` span, so a quote or
    ``//`` in it is text.
    """
    n = len(text)
    templates: list[int] = []  # open ``{`` count inside each enclosing ``${``
    prev = ""  # the last code character that isn't whitespace
    jsx = 0  # the next ``jsx_text`` span
    i = 0
    while i < n:
        while jsx < len(jsx_text) and jsx_text[jsx][0] < i:
            jsx += 1
        if jsx < len(jsx_text) and jsx_text[jsx][0] == i:
            end = max(jsx_text[jsx][1], i + 1)
            yield i, end, "jsx"
            i = end
            prev = ">"
            continue
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            yield i, end, "comment"
            i = end
        elif ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            yield i, end, "comment"
            i = end
        elif ch in "'\"":
            end = _quote_end(text, i)
            yield i, end, "string"
            i = end
            prev = "a"
        elif ch == "`" or (ch == "}" and templates and templates[-1] == 0):
            if ch == "}":
                templates.pop()
            end, substitution = _template_end(text, i + 1)
            yield i, end, "template"
            i = end
            if substitution:
                templates.append(0)
                prev = "{"
            else:
                prev = "a"
        elif ch == "/" and _regex_allowed(text, i, prev):
            end = _regex_end(text, i)
            if end is None:
                prev = ch
                i += 1
            else:
                yield i, end, "regex"
                i = end
                prev = "a"
        else:
            if templates and ch == "{":
                templates[-1] += 1
            elif templates and ch == "}":
                templates[-1] -= 1
            if not ch.isspace():
                prev = ch
            i += 1


def _quote_end(text: str, start: int) -> int:
    """The index after a quoted string's closing quote, or its line break."""
    quote = text[start]
    j = start + 1
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            return j
        j += 1
    return len(text)


def _template_end(text: str, j: int) -> tuple[int, bool]:
    """Scan template text from ``j``: the index after the closing backtick or
    after the ``${`` that opens a substitution, and whether it was one."""
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1, False
        if c == "$" and text.startswith("{", j + 1):
            return j + 2, True
        j += 1
    return len(text), False


def _regex_allowed(text: str, i: int, prev: str) -> bool:
    if not prev or prev in _REGEX_AFTER:
        return True
    if not (prev.isalpha() or prev in "_$"):
        return False
    k = i - 1
    while k >= 0 and text[k].isspace():
        k -= 1
    end = k + 1
    while k >= 0 and (text[k].isalnum() or text[k] in "_$"):
        k -= 1
    return text[k + 1 : end] in _REGEX_AFTER_WORDS


def _regex_end(text: str, start: int) -> int | None:
    """The index after a regex literal's flags, or None when its line ends first."""
    in_class = False
    j = start + 1
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return None
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            return j
        j += 1
    return None


__all__ = [
    "blank_spans",
    "code_text",
    "literal_spans",
    "scan_code",
    "strip_js_ts_comments",
]
