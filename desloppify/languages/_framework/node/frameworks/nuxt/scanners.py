"""Nuxt scanners: legacy process flags, data composables outside setup, private runtime config.

Text scanners over the package that depends on ``nuxt`` (3 and later).
Components are read through their code view.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from desloppify.languages._framework.node.js_classes import matching, split_top_level
from desloppify.languages._framework.node.js_functions import (
    FunctionLiteral,
    call_arguments,
    function_at,
)
from desloppify.languages._framework.node.js_text import code_text

from ..component_sources import SourceFile, body_span, dependency_major, package_sources

_PROCESS_FLAG_RE = re.compile(
    r"(?<![\w$.])process\.(?P<flag>client|server|browser|dev)\b(?!\s*=[^=])"
)
# Node-side code: the config, local modules and the Nitro server.
_NODE_SIDE_RE = re.compile(
    r"(?:^|/)(?:server|modules)/|(?:^|/)nuxt\.config\.[cm]?[jt]s$"
)

_DATA_COMPOSABLE_RE = re.compile(
    r"(?<![\w$.])(?P<name>use(?:Lazy)?(?:Fetch|AsyncData))\s*\("
)
_NAMED_FUNCTION_RE = re.compile(
    r"(?<![\w$.])(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*\("
    r"|\b(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*(?::[^=;]+?)?=\s*(?=(?:async\b|function\b|\(|[A-Za-z_$][\w$]*\s*=>))"
)
# Callbacks that run after setup has returned.
_LATE_CALLBACK_RE = re.compile(
    r"(?<![\w$.])(?:onMounted|onBeforeMount|onUpdated|onBeforeUpdate|onActivated|onDeactivated"
    r"|onBeforeUnmount|onUnmounted|watch|watchEffect|watchPostEffect|nextTick|setTimeout|setInterval"
    r"|addEventListener|onNuxtReady)\s*\("
)
_TEMPLATE_BINDING_RE = r"""(?:@|v-on:|:|v-bind:)[\w.:-]+\s*=\s*(?P<q>["'])[^"']*(?<![\w$.]){name}\b[^"']*(?P=q)"""

_CONFIG_NAMES = tuple(
    f"nuxt.config.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
)
_RUNTIME_CONFIG_RE = re.compile(r"(?<![\w$.])runtimeConfig\s*:\s*\{")
_CONFIG_KEY_RE = re.compile(r"\s*([A-Za-z_$][\w$]*)\s*(?=:|$)")
_QUOTED_KEY_RE = re.compile(r"""(['"])([\w$-]+)\1\s*$""")
_RUNTIME_CONFIG_CALL_RE = re.compile(r"(?<![\w$.])useRuntimeConfig\s*\(\s*\)")
_ASSIGNED_RE = re.compile(
    r"\b(?:const|let|var)\s+(?:(?P<name>[A-Za-z_$][\w$]*)|\{(?P<keys>[^}]*)\})\s*(?::[^=;]+?)?=\s*$"
)
_PUBLIC_KEYS = frozenset({"public", "app"})


def _nuxt3_or_later(path: Path) -> bool:
    major = dependency_major(path, "nuxt")
    return major is None or major >= 3


def _relative(source: SourceFile, root: Path) -> str:
    try:
        return source.full.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return source.path.replace("\\", "/")


def scan_legacy_process_flags(path: Path) -> tuple[list[dict], int]:
    """``process.client``/``process.server``/``process.browser``/``process.dev`` in Nuxt 3+ app code.

    Nuxt 3 replaced them with ``import.meta.client``, ``import.meta.server``
    and ``import.meta.dev``; the ``process`` forms are deprecated shims. The
    Nitro server, local modules and the config run in Node and aren't read.
    """
    if not _nuxt3_or_later(path):
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path):
        if _NODE_SIDE_RE.search(_relative(source, path)):
            continue
        scanned += 1
        matches = list(_PROCESS_FLAG_RE.finditer(source.code))
        if matches:
            entries.append(
                {
                    "file": source.path,
                    "line": source.line(matches[0].start()),
                    "flag": f"process.{matches[0].group('flag')}",
                    "count": len(matches),
                }
            )
    return entries, scanned


def _markup(source: SourceFile) -> str:
    component = source.component
    if component is None:
        return ""
    out = list(source.text)
    for block in component.blocks:
        for k in range(block.start, block.end):
            out[k] = " "
    return "".join(out)


def _late_callbacks(code: str) -> Iterator[FunctionLiteral]:
    for _match, args in call_arguments(code, _LATE_CALLBACK_RE):
        for start, _end in args:
            function = function_at(code, start)
            if function is not None:
                yield function


def _named_functions(code: str) -> Iterator[tuple[str, int, FunctionLiteral]]:
    for match in _NAMED_FUNCTION_RE.finditer(code):
        name = match.group("name") or match.group("var")
        start = match.start() if match.group("name") else match.end()
        function = function_at(code, start)
        if function is not None:
            yield name, match.start("name" if match.group("name") else "var"), function


def _inside(spans: list[tuple[int, int]], offset: int) -> bool:
    return any(start <= offset < end for start, end in spans)


def scan_data_composables_outside_setup(path: Path) -> tuple[list[dict], int]:
    """``useFetch``/``useAsyncData`` called from an event handler, watcher or lifecycle hook.

    They are composables: called after setup they can't register with the
    component, the SSR payload or the cache, and Nuxt warns that the
    component is already mounted. ``$fetch`` is what a handler should call.
    A function named ``use*`` is a composable and may call them; a local
    function only ever called during setup is fine too.
    """
    if not _nuxt3_or_later(path):
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path, scripts=False):
        if not source.name.endswith(".vue"):
            continue
        scanned += 1
        code = source.code
        calls = list(_DATA_COMPOSABLE_RE.finditer(code))
        if not calls:
            continue
        late = [body_span(code, f) for f in _late_callbacks(code)]
        markup = _markup(source)
        named = [
            (name, at, body_span(code, f))
            for name, at, f in _named_functions(code)
            if not name.startswith("use") and name != "setup"
        ]
        flagged: list[tuple[int, int]] = list(late)
        # A named function is late when the template binds it, it's passed
        # as a value, or a late callback (or another late function) calls it.
        changed = True
        late_names: set[str] = set()
        while changed:
            changed = False
            for name, at, span in named:
                if name in late_names:
                    continue
                if _is_late(code, markup, name, at, span, flagged):
                    late_names.add(name)
                    flagged.append(span)
                    changed = True
        for call in calls:
            if _inside(flagged, call.start()):
                entries.append(
                    {
                        "file": source.path,
                        "line": source.line(call.start()),
                        "composable": call.group("name"),
                    }
                )
    return entries, scanned


def _is_late(
    code: str,
    markup: str,
    name: str,
    at: int,
    span: tuple[int, int],
    late: list[tuple[int, int]],
) -> bool:
    if markup and re.search(_TEMPLATE_BINDING_RE.format(name=re.escape(name)), markup):
        return True
    for ref in re.finditer(rf"(?<![\w$.]){re.escape(name)}\b", code):
        if ref.start() == at or span[0] <= ref.start() < span[1]:
            continue
        after = code[ref.end() : ref.end() + 40].lstrip()
        if after.startswith("("):
            if _inside(late, ref.start()):
                return True
            continue
        if after.startswith(("=", ":")) and not after.startswith(("==", "=>")):
            continue  # an assignment or an object key, not a use
        return True
    return False


def _config_key(text: str, code: str, start: int, end: int) -> str | None:
    """The key of one ``runtimeConfig`` property (``key: value``, ``'key': value`` or shorthand)."""
    name = _CONFIG_KEY_RE.match(code, start, end)
    if name is not None:
        return name.group(1)
    colon = code.find(":", start, end)
    if colon == -1:
        return None  # a spread or something else that isn't a plain key
    quoted = _QUOTED_KEY_RE.search(text, start, colon)
    return quoted.group(2) if quoted else None


def private_runtime_config_keys(path: Path) -> frozenset[str]:
    """Top-level ``runtimeConfig`` keys of the package's nuxt.config, other than ``public`` and ``app``."""
    for name in _CONFIG_NAMES:
        try:
            text = (path / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        code = code_text(text)
        match = _RUNTIME_CONFIG_RE.search(code)
        if match is None:
            return frozenset()
        open_at = match.end() - 1
        keys = set()
        for start, end in split_top_level(code, open_at + 1, matching(code, open_at)):
            key = _config_key(text, code, start, end)
            if key is not None:
                keys.add(key)
        return frozenset(keys - _PUBLIC_KEYS)
    return frozenset()


def _client_side(source: SourceFile) -> bool:
    name = source.name
    if name.endswith(".vue"):
        return not name.endswith(".server.vue")
    return bool(re.search(r"\.client\.[cm]?[jt]s$", name))


def _read_keys(code: str, call: re.Match[str]) -> Iterator[tuple[str, int]]:
    """The config keys read from one ``useRuntimeConfig()`` call."""
    after = re.compile(r"\s*(?:\?\.|\.)\s*([A-Za-z_$][\w$]*)").match(code, call.end())
    if after:
        yield after.group(1), after.start(1)
        return
    line_start = code.rfind("\n", 0, call.start()) + 1
    assigned = _ASSIGNED_RE.search(code, line_start, call.start())
    if assigned is None:
        return
    if assigned.group("name"):
        for ref in re.finditer(
            rf"(?<![\w$.]){re.escape(assigned.group('name'))}\s*(?:\?\.|\.)\s*([A-Za-z_$][\w$]*)",
            code,
        ):
            yield ref.group(1), ref.start(1)
        return
    offset = assigned.start("keys")
    for part in assigned.group("keys").split(","):
        key = re.match(r"\s*([A-Za-z_$][\w$]*)", part)
        if key:
            yield key.group(1), offset + key.start(1)
        offset += len(part) + 1


def scan_private_runtime_config_in_client(path: Path) -> tuple[list[dict], int]:
    """A private ``runtimeConfig`` key read in code that runs in the browser.

    Only ``runtimeConfig.public`` reaches the client: a private key is
    undefined there, so a component reading one renders differently on the
    server and in the browser. Keys come from the package's nuxt.config;
    components (not ``.server.vue``) and ``.client`` plugins are read.
    """
    if not _nuxt3_or_later(path):
        return [], 0
    private = private_runtime_config_keys(path)
    if not private:
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path):
        if not _client_side(source):
            continue
        scanned += 1
        seen: set[str] = set()
        for call in _RUNTIME_CONFIG_CALL_RE.finditer(source.code):
            for key, at in _read_keys(source.code, call):
                if key in private and key not in seen:
                    seen.add(key)
                    entries.append(
                        {"file": source.path, "line": source.line(at), "key": key}
                    )
    return entries, scanned


__all__ = [
    "private_runtime_config_keys",
    "scan_data_composables_outside_setup",
    "scan_legacy_process_flags",
    "scan_private_runtime_config_in_client",
]
