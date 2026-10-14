"""HTML sinks that only receive escaped or sanitized markup are not XSS issues."""

from __future__ import annotations

import textwrap

import pytest

from desloppify_ts.languages.typescript.detectors.security.detector import (
    detect_ts_security,
)
from desloppify_ts.languages.typescript.syntax.tree import get_parser

needs_treesitter = pytest.mark.skipif(
    get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar"
)

_KINDS = ("innerHTML_assignment", "dangerously_set_inner_html")


def _sink_lines(tmp_path, name: str, code: str) -> list[int]:
    source = tmp_path / name
    source.write_text(textwrap.dedent(code))
    entries = detect_ts_security([str(source)], None).entries
    return [e["detail"]["line"] for e in entries if e["detail"]["kind"] in _KINDS]


# ── innerHTML ──────────────────────────────────────────────


@needs_treesitter
def test_inner_html_template_with_escaped_interpolations_is_skipped(tmp_path):
    code = """\
    function escapeHtml(value) { return String(value).replaceAll('<', '&lt;') }
    el.innerHTML = `<b>${escapeHtml(name)}</b> ${escapeHtml(count)}`
    """
    assert _sink_lines(tmp_path, "a.mjs", code) == []


@needs_treesitter
def test_inner_html_map_join_and_conditional_are_followed(tmp_path):
    code = """\
    list.innerHTML = items.length > 0
      ? items.map((item) => `<li>${escapeHtml(item.label)}</li>`).join('')
      : '<p>None</p>'
    grid.innerHTML = [{ meta: `${n} open` }].map((card) => `
      <p>${escapeHtml(card.meta)}</p>
    `).join('')
    """
    assert _sink_lines(tmp_path, "a.ts", code) == []


@needs_treesitter
def test_inner_html_through_const_and_sanitizer_is_skipped(tmp_path):
    code = """\
    const html = DOMPurify.sanitize(raw)
    el.innerHTML = html
    other.innerHTML = 'static ' + escapeHtml(x)
    """
    assert _sink_lines(tmp_path, "a.ts", code) == []


@needs_treesitter
def test_inner_html_with_raw_interpolation_still_fires(tmp_path):
    code = """\
    el.innerHTML = `<b>${escapeHtml(name)}</b> ${comment}`
    list.innerHTML = items.map((item) => `<li>${item.label}</li>`).join('')
    out.innerHTML = userInput
    let html = escapeHtml(a)
    html = raw
    box.innerHTML = html
    """
    assert _sink_lines(tmp_path, "a.ts", code) == [1, 2, 3, 6]


# ── dangerouslySetInnerHTML ────────────────────────────────


@needs_treesitter
def test_json_ld_script_is_skipped(tmp_path):
    code = """\
    export const Page = () => (
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(data).replace(/</g, '\\\\u003c'),
        }}
      />
    )
    """
    assert _sink_lines(tmp_path, "page.tsx", code) == []


@needs_treesitter
def test_json_stringify_outside_json_ld_still_fires(tmp_path):
    code = """\
    export const A = () => <div dangerouslySetInnerHTML={{ __html: JSON.stringify(d) }} />
    export const B = () => (
      <script dangerouslySetInnerHTML={{ __html: JSON.stringify(d) }} />
    )
    """
    assert _sink_lines(tmp_path, "page.tsx", code) == [1, 3]


@needs_treesitter
def test_dompurify_value_is_skipped(tmp_path):
    code = """\
    import DOMPurify from 'dompurify'
    export function Html({ content, hydrated }) {
      const clean = useMemo(
        () => (hydrated ? getPurifier().sanitize(content, OPTIONS) : ''),
        [hydrated, content],
      )
      return <div dangerouslySetInnerHTML={{ __html: clean }} />
    }
    export const B = ({ c }) => <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(c) }} />
    """
    assert _sink_lines(tmp_path, "html.tsx", code) == []


@needs_treesitter
def test_unsanitized_value_still_fires(tmp_path):
    code = """\
    export function Html({ content }) {
      const clean = useMemo(() => content.trim(), [content])
      return <div dangerouslySetInnerHTML={{ __html: clean }} />
    }
    export const B = ({ c }) => <div dangerouslySetInnerHTML={{ __html: c }} />
    export const C = (props) => <div dangerouslySetInnerHTML={props.html} />
    """
    assert _sink_lines(tmp_path, "html.tsx", code) == [3, 5, 6]
