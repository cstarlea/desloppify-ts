"""String- and comment-aware TypeScript character scanner."""

from __future__ import annotations

from collections.abc import Generator

from desloppify.languages._framework.node.js_text import code_text


def scan_code(
    text: str, start: int = 0, end: int | None = None
) -> Generator[tuple[int, str, bool], None, None]:
    """Yield ``(index, char, in_string)`` for ``text[start:end]``.

    ``in_string`` is True for anything that isn't code: string, template and
    regex literal text, and comments (see ``js_text.code_text``).
    """
    limit = end if end is not None else len(text)
    chunk = text[start:limit]
    code = code_text(chunk)
    for offset, ch in enumerate(chunk):
        yield (start + offset, ch, code[offset] != ch)
