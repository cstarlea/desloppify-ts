"""Round-trip property tests for every TypeScript fixer (roadmap 2.4, FX-19).

Each fixer runs the way ``autofix`` runs it: its registry detector scans a
small project in ``tmp_path``, the fixer writes the files, and then detector
and fixer run a second time. The project is a corpus of adversarial
TypeScript/TSX files: no semicolons, comments and JSDoc in odd places,
unicode and emoji (tsc reports UTF-16 columns), CRLF, a UTF-8 BOM, mixed line
endings, a missing final newline, JSX, generics, template strings, overloads,
export forms and nested destructuring, plus the cases behind the FX-* rows of
the roadmap appendix.

Properties checked for every fixer and every file:

1. The output has no syntax errors (the corpus starts with none).
2. Running detector and fixer again changes nothing. The corpus avoids
   cascades within one fixer (a removal that makes another declaration of the
   same kind unused), so any second-run change is a fixer bug.
3. Bytes are kept apart from the edited spans: removal fixers only delete
   (the output is a subsequence of the input) and the params fixer only
   inserts. CRLF stays CRLF and LF stays LF, the BOM stays, the file mode is
   unchanged and a final newline is kept. Every string and template literal
   in the output appears verbatim in the input, so no edit lands inside one.
4. With a real ``tsc``, no new diagnostics other than unused-symbol ones
   appear. That layer is skipped when no tsc is found (install the pinned one
   with ``npm ci --prefix desloppify/languages/typescript/tests/golden/node``).

All findings come from the real detectors; no entries are written by hand.
The unused-imports/vars/params detector runs in two layers: ``tsc`` (the
pinned or a global compiler) and ``fallback`` (tsc hidden, so the detector
uses its source-based heuristic). The logs and smells detectors need no
external tools.

Inputs that still trip a fixer bug live in ``_KNOWN_BUGS``, outside the main
corpus, and run under a strict xfail until the fixer is fixed.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from collections import Counter
from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.unused as unused_mod
from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript._fixers import get_ts_fixers
from desloppify.languages.typescript.fixers.params import prefix_unused_params
from desloppify.languages.typescript.syntax.nodes import byte_offset
from desloppify.languages.typescript.syntax.tree import get_parser, parse_text
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    get_parser("tsx") is None,
    reason="the syntax-tree fixers need tree-sitter (pip install 'desloppify[full]')",
)

_BOM = "\ufeff"
_PINNED_TSC = Path(__file__).parent / "golden" / "node" / "node_modules" / ".bin" / "tsc"


def _find_tsc() -> str | None:
    if _PINNED_TSC.is_file():
        return str(_PINNED_TSC)
    return shutil.which("tsc")


TSC = _find_tsc()
_needs_tsc = pytest.mark.skipif(TSC is None, reason="no tsc found")

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

_TSCONFIG = """\
{
  "compilerOptions": {
    "strict": true,
    "noEmit": true,
    "target": "es2022",
    "lib": ["es2022"],
    "types": [],
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "preserve",
    "skipLibCheck": true
  },
  "include": ["src"]
}
"""

# Support files: never edited, they only make the corpus type-check.
_SUPPORT = {
    "src/globals.d.ts": """\
declare namespace JSX {
  type Element = unknown;
  interface IntrinsicElements { [name: string]: unknown }
}
declare const console: {
  log(...values: unknown[]): void;
  info(...values: unknown[]): void;
  warn(...values: unknown[]): void;
  debug(...values: unknown[]): void;
};
declare function use(...values: unknown[]): void;
declare function save(): boolean;
declare function promise(): Promise<void>;
""",
    "src/m.ts": """\
export const a = 1;
export const b = 2;
export const c = 3;
export const d = 4;
export type T = { n: number };
export interface I { s: string }
export default function def() { return 0; }
export function fn(...values: unknown[]) { return values.length; }
""",
    "src/side.ts": "export const sideEffect = 1;\n",
    "src/react.ts": """\
export function useEffect(effect: () => void, deps?: unknown[]): void {
  use(effect, deps);
}
export function useState<S>(initial: S): [S, (next: S) => void] {
  return [initial, (next) => use(next)];
}
""",
}

# Written with "\n"; ``_ENCODINGS`` below turns some into CRLF/BOM files.
_CORPUS = {
    # No semicolons: removals must not let a neighbour continue the previous
    # statement (ASI hazards).
    "src/asi.ts": """\
import { a, b } from './m'
import def from './m'
import './side'
import { c } from './m'
const unusedAsi = 1
let keep = a
;[keep].forEach((item, index) => use(item))
const TAG = '[Asi]'
console.log(`${TAG} start`)
const w = keep
console.log('[Asi] w', w);
(use)(w)
const q = keep
if (q) {}
(use)(q)
const z = keep
console.log('[Asi] z', z)
;(function () { use(c) })()
if (keep) {
} else {
}
export function run(x: number, y: number) {
  console.log('[Asi] run', x)
  return x
}
""",
    # FX-6: `}` and commas in comments; JSDoc in odd places.
    "src/comments.ts": """\
import {
  a, // a comment with } and , in it
  /* b, */ b,
  c /* trailing } */,
} from './m';
import type { T /* , I */ } from './m';
import { d as dee, fn as d2 } from './m'; // alias
/** JSDoc for an unused constant */
const unusedDoc = 1;
/**
 * Documented helper.
 */
function unusedHelper(/* inline */ value: number /* after */) {
  return value;
}
export const used = a + /* } */ c + dee;
export function api(
  // a line comment before the param
  input: string, /** doc */ options?: { verbose: boolean },
): string {
  return input;
}
""",
    # Astral characters before a name make tsc's UTF-16 column differ from
    # the byte and code-point columns.
    "src/unicode.ts": """\
import { a, b } from './m'; const ｓ = '😀😀'; // emoji
const label = '🎉 café 日本語'; const unusedEmoji = '🙈'; export const shown = label + a;
export function greet(名前: string, ひ: number) { return `👋 ${名前}`; }
console.log('🔍 [Unicode] ✨', shown);
const separators = 'a\u2028b\u2029c'; export const width = separators.length;
const afterSeparators = 1; export function later(k: number, unusedK: number) { return k; }
export function f(p: number) { return 0; }
export const filler = 0;
export function g(p: number) { return p; }
""",
    "src/crlf.ts": """\
import { a, b } from './m';

const unusedCrlf = 1;
export const value = a;
export function handler(event: string, context: number) {
  // debug: trace the event
  console.debug('[Crlf] event', event);
  if (event) {
  } else if (value) {
  } else {
  }
  return event;
}
""",
    # FX-18: a BOM in front of a line-1 import.
    "src/bom.ts": """\
import { a, b, c } from './m';
const unusedBom = '\\ufeff';
export const total = a + c;
console.info('[Bom] total', total);
""",
    "src/crlf_bom.tsx": """\
import { useEffect, useState } from './react';
import { a, b } from './m';

export function Widget<T,>({ items, unusedProp }: { items: T[]; unusedProp?: string }) {
  const [count, setCount] = useState(0);
  // Load data on mount
  useEffect(() => {
    // nothing yet
  }, []);
  return (
    <ul title={`count ${count}`}>
      {items.map((item, index) => <li key={String(item)}>{a}</li>)}
    </ul>
  );
}
""",
    # JSX, generics, multi-line constructs; FX-2 (a log as an arrow body).
    "src/component.tsx": """\
import { useEffect } from './react';
import * as M from './m';
import def, { c } from './m';

type Props<T extends object> = { data: T; render: (value: T) => JSX.Element };

export function List<T extends object>({ data, render }: Props<T>) {
  useEffect(() => {}, [data]);
  const unusedMemo = <div className="x">{'}'}</div>;
  const shown = data && (
    <section>
      {/* console.log('[Jsx] not a call') */}
      <button onClick={() => console.log('[Jsx] click', c)}>{render(data)}</button>
    </section>
  );
  return shown;
}
export const Generic = <T,>(value: T, extra: number): T => value;
""",
    # FX-4: tags, `if` chains and useEffect inside template strings.
    "src/templates.ts": """\
import { a, b } from './m';
const DEBUG_TAG = '[Tpl]';
export const multi = `line one ${a}
console.log('[Tpl] inside a template')
if (x) {
} else {
}
const notReal = 1, ${'}'}`;
export function report(value: number, unused: string) {
  console.log(`${DEBUG_TAG} value=${value}`, `nested ${`deep ${value}`}`);
  console.info('[Tpl] obj', { value, s: `}${value}{` });
  return String.raw`\\n${value}`;
}
const unusedTpl = `${a}`;
""",
    "src/overloads.ts": """\
export function parse(input: string): number;
export function parse(input: number, radix: number): number;
export function parse(input: string | number, radix?: number): number {
  return Number(input);
}
export class Service {
  constructor(private readonly client: string, unusedCtor: number) {}
  method(a: string): void;
  method(a: string, b?: number): void;
  method(a: string, b?: number): void {
    console.warn('[Service] method', a);
  }
}
""",
    "src/exports.ts": """\
import { a, b, c, d } from './m';
import type { I } from './m';
import def from './m';
export { b };
export { c as see };
export default def;
export type { I };
export * from './side';
export * as ns from './m';
export const ex = 1, alsoExported = 2;
const local = 3, unusedLocal = 4;
export { local };
export declare const declared: number;
export enum Color { Red, Green }
export abstract class Base { abstract run(arg: string): void }
""",
    # FX-7, FX-8, FX-9: declarators, nested destructuring, `_` prefixes.
    "src/destructure.ts": """\
declare const source: { a: number; b: { c: number; d: number[] }; 'quoted-key': string; e?: number };
const { a, b: { c, d: [first, second] }, 'quoted-key': quoted, e = 1 } = source;
export const total = a + first;
const { a: renamed, ...rest } = source;
export const restKeys = Object.keys(rest);
let one = 1, two = one + 1, three = "a, b = c";
export { two };
const [x, , y = 2] = [1, 2, 3];
export const ys = y;
var hoisted = 1, alsoHoisted = 'const c_onst = 1';
export { alsoHoisted };
export function scoped() {
  var p = 1, q = 2;
  return 0;
}
export function pick({ keep, drop }: { keep: number; drop: number }, [h, t]: number[]) {
  return keep + h;
}
export function withDefaults(
  { alpha = 1, beta: { gamma } = { gamma: 2 } }: { alpha?: number; beta?: { gamma: number } } = {},
) {
  return 0;
}
try { use(1); } catch ({ message, stack }: any) { use(message); }
""",
    # FX-1, FX-2, FX-4: logs that must stay, and ones that can go.
    "src/logs.ts": """\
const TAG = '[Logs]';
let counter = 0;
export function main(flag: boolean, items: number[]) {
  if (flag) console.log('[Logs] unbraced if body');
  else console.log('[Logs] unbraced else body');
  for (const item of items) console.log('[Logs] loop body', item);
  const value = flag ? console.log('[Logs] ternary') : 0;
  const result = console.log('[Logs] assigned');
  items.forEach((item) => console.log('[Logs] arrow body', item));
  console.log('[Logs] side effect', counter++);
  console.log('[Logs] call arg', compute());
  console.log('[Logs] pure arg', JSON.stringify(items), items.length);
  console.log('[Logs] first'); console.log('[Logs] second'); use(value, result);
  console.log(
    '[Logs] multi-line',
    items,
  );
  const s = "console.log('[Logs] in a string')"; // console.log('[Logs] in a comment')
  /* console.log('[Logs] in a block comment') */
  console.log(`${TAG} template tag ${s}`);
  // temp debug output
  console.debug('[Logs] with debug comment');
  promise().catch(() => {});
  return value;
}
function compute() { return 1; }
function log(message: string) {
  console.log('[Logs] wrapper', message);
}
export { log };
""",
    # FX-3, FX-10: only all-empty chains with pure conditions may go.
    "src/ifchain.ts": """\
export function check(x: number, y: number) {
  if (x) {
  } else if (y) {
  } else {
    use('not empty');
  }
  if (save()) {
  }
  if (x > 1) {
  } else {
    // a comment keeps it
  }
  if (x) {} else if (y) {}
  if (y) {
  }
  while (y) if (x) {} else {}
  label: if (x) {}
  promise().catch(() => {}).then(() => {
  });
  return 0;
}
""",
    # FX-17: dead effects next to comments, code and template strings.
    "src/effects.tsx": """\
import { useEffect } from './react';

export function Effects({ id }: { id: string }) {
  // Sync the id
  useEffect(() => {
    // TODO: implement
  }, [id]);
  useEffect(() => {
    /* } */
  }, []);
  useEffect(() => {
    use(id);
  }, [id]);
  return <p>{id}</p>;
}
""",
    # Dead effects inside a template string, and sharing a line with code.
    "src/effect_in_template.tsx": """\
import { useEffect } from './react';
export function Doc() {
  useEffect(() => { use(1); }, []);
  useEffect(() => {
  }, []);
  return `
useEffect(() => {
}, []);
`;
}
""",
    "src/effect_same_line.tsx": """\
import { useEffect } from './react';
export function Inline() {
  useEffect(() => {}, []); const later = 1;
  return later;
}
""",
    # 2.29: every JavaScript line break (U+2028, U+2029, a lone CR) and the
    # characters str.splitlines() also breaks at (VT, FF, U+0085), in strings,
    # comments and a template, before every kind of finding. Detectors and
    # fixers must agree on the lines after them (``_FULLY_FIXED``).
    "src/breaks.ts": """\
import { useEffect } from './react';
const ls = 'a\u2028b'; const ps = 'c\u2029d'; /* e\u2028f\u2029g\rh\x0bi\x0cj\x85k */
const ctl = 'v\x0bf\x0cn\x85'; export const seps = ls + ps + ctl;\rconst unusedAfterCr = 1;
// a comment with \x0b, \x0c and \x85 in it
export const tpl = `x\ry\u2028z`;
import { d } from './m';
console.log('[Breaks] after separators', seps);
const unusedBreaks = 2;
export function breaks(x: number, unusedArg: number) {
  if (x) {
  } else {
  }
  useEffect(() => {}, []);
  console.log('[Breaks] in function', x);
  return tpl;
}
""",
    # The last statement goes and there is no final newline.
    "src/no_eol.ts": "import { a, b } from './m';\nexport const k = a;\nconst unusedLast = 2;",
}

_CRLF = {"src/crlf.ts", "src/crlf_bom.tsx"}
_WITH_BOM = {"src/bom.ts", "src/crlf_bom.tsx"}
# Mostly LF with one CRLF line: neither ending may be rewritten to the other.
_MIXED = {"src/mixed.ts": "import { a, b } from './m';\r\nconst unusedMixed = 1;\nexport const m = a;\n"}
_EXECUTABLE = {"src/asi.ts"}
# Files where every finding can be fixed: a second detection must find nothing.
_FULLY_FIXED = {"src/breaks.ts"}



# Files that trip a fixer bug, kept out of the corpus so they don't hide other
# regressions; ``test_known_fixer_bugs`` runs each one under a strict xfail.
_KNOWN_BUGS: list = []


def _corpus_bytes() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name, text in _CORPUS.items():
        if name in _CRLF:
            text = text.replace("\n", "\r\n")
        if name in _WITH_BOM:
            text = _BOM + text
        files[name] = text.encode("utf-8")
    for name, text in _MIXED.items():
        files[name] = text.encode("utf-8")
    return files


def _write_project(root: Path, corpus: dict[str, bytes]) -> None:
    (root / "tsconfig.json").write_text(_TSCONFIG)
    for name, text in _SUPPORT.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name, data in corpus.items():
        path = root / name
        path.write_bytes(data)
        os.chmod(path, 0o755 if name in _EXECUTABLE else 0o640)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

_LITERALS = frozenset({"string", "template_string"})


def _is_subsequence(short: bytes, long: bytes) -> bool:
    it = iter(long)
    return all(byte in it for byte in short)


def _literals(text: str, path: str) -> set[str]:
    parsed = parse_text(text, path)
    assert parsed is not None
    found: set[str] = set()
    stack = [parsed.root]
    while stack:
        node = stack.pop()
        if node.type in _LITERALS:
            found.add(parsed.text(node))
        stack.extend(node.named_children)
    return found


def _byte_problems(name: str, before: bytes, after: bytes, *, inserts: bool) -> list[str]:
    problems = []
    if inserts and not _is_subsequence(before, after):
        problems.append("the rename did more than insert text")
    if not inserts and not _is_subsequence(after, before):
        problems.append("the removal did more than delete text")
    if before.count(b"\r\n") != before.count(b"\n"):  # LF or mixed: no new CRs
        if after.count(b"\r") > before.count(b"\r"):
            problems.append("LF line endings became CRLF")
    elif after.count(b"\r\n") != after.count(b"\n"):
        problems.append("CRLF line endings became LF")
    if before.startswith(_BOM.encode()) != after.startswith(_BOM.encode()):
        problems.append("the BOM was not kept")
    if before.endswith(b"\n") and after and not after.endswith(b"\n"):
        problems.append("the final newline was lost")
    text_before = before.decode("utf-8").removeprefix(_BOM)
    text_after = after.decode("utf-8").removeprefix(_BOM)
    edited = _literals(text_after, name) - _literals(text_before, name)
    if edited:
        problems.append(f"string or template literals changed: {sorted(edited)}")
    return problems


_DIAGNOSTIC_RE = re.compile(r"^(.+?)\(\d+,\d+\): error (TS\d+): (.*)$")
_UNUSED_CODES = frozenset({"TS6133", "TS6138", "TS6192", "TS6196", "TS6198", "TS6199", "TS6205"})


def _other_diagnostics(output: str) -> Counter:
    """tsc errors other than unused-symbol ones, without positions (edits move lines)."""
    found: Counter = Counter()
    for line in output.splitlines():
        match = _DIAGNOSTIC_RE.match(line)
        if match and match.group(2) not in _UNUSED_CODES:
            found[match.groups()] += 1
    return found


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_INSERTING_FIXERS = frozenset({"unused-params"})
# The unused detector runs with tsc and with its fallback. The others don't
# use tsc to detect, so they run once, with the tsc check when there is one.
ROUND_TRIPS = [
    pytest.param(fixer, layer, id=f"{fixer}-{layer}", marks=[_needs_tsc] if layer == "tsc" else [])
    for fixer in ("unused-imports", "unused-vars", "unused-params")
    for layer in ("tsc", "fallback")
] + [
    pytest.param(fixer, "tsc" if TSC else "no-tsc", id=fixer)
    for fixer in ("debug-logs", "empty-if-chain", "dead-useeffect")
]


class _Tsc:
    """Points the unused detector at ``TSC`` (or hides it) and records its output."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, *, available: bool) -> None:
        self.root = root
        self.outputs: list[str] = []
        original = unused_mod._run_tsc_unused_check

        def resolve(*_start_dirs: Path) -> list[str]:
            if not available or TSC is None:
                raise OSError("tsc hidden by the round-trip tests")
            return [TSC]

        def run(project_root: Path, tsconfig_path: Path):
            result = original(project_root, tsconfig_path)
            self.outputs.append(f"{result.stdout}\n{result.stderr}")
            return result

        monkeypatch.setattr(unused_mod, "_resolve_tsc_command", resolve)
        monkeypatch.setattr(unused_mod, "_run_tsc_unused_check", run)

    def diagnostics(self, since: int) -> Counter:
        """Errors from the first run after ``since`` runs, running tsc if the detector didn't."""
        if len(self.outputs) <= since:
            unused_mod._run_tsc_unused_check(self.root, self.root / "tsconfig.json")
        return _other_diagnostics(self.outputs[since])


def _snapshot(root: Path, names) -> dict[str, tuple[bytes, int]]:
    return {
        name: ((root / name).read_bytes(), stat.S_IMODE((root / name).stat().st_mode))
        for name in names
    }


def _round_trip(
    fixer_name: str, layer: str, root: Path, corpus: dict[str, bytes], monkeypatch
) -> tuple[int, list[str]]:
    """Run detector and fixer twice; return how many files the first run fixed and the problems."""
    _write_project(root, corpus)
    for name, data in corpus.items():
        # Corpus mistakes fail with pytest.fail, not AssertionError, so they
        # can't satisfy a known bug's xfail.
        if count_syntax_errors(data.decode("utf-8").removeprefix(_BOM), name):
            pytest.fail(f"the corpus file {name} should parse")
    with_tsc = layer == "tsc"
    tsc = _Tsc(root, monkeypatch, available=with_tsc)
    config = get_ts_fixers()[fixer_name]

    with runtime_scope(RuntimeContext(project_root=root)):
        clear_source_file_cache_for_tests()
        before = _snapshot(root, corpus)
        # The unused detector's own tsc runs double as the type check.
        runs = len(tsc.outputs)
        first_entries = config.detect(root)
        before_diagnostics = tsc.diagnostics(runs) if with_tsc else Counter()
        if before_diagnostics:
            pytest.fail(f"the corpus should type-check: {sorted(before_diagnostics)}")

        first = config.fix(first_entries, dry_run=False)
        clear_source_file_cache_for_tests()
        after = _snapshot(root, corpus)

        runs = len(tsc.outputs)
        second_entries = config.detect(root)
        after_diagnostics = tsc.diagnostics(runs) if with_tsc else Counter()
        second = config.fix(second_entries, dry_run=False)
        clear_source_file_cache_for_tests()
        again = _snapshot(root, corpus)

    problems: list[str] = []
    for name in corpus:
        (old, old_mode), (new, new_mode) = before[name], after[name]
        text = new.decode("utf-8").removeprefix(_BOM)
        errors = count_syntax_errors(text, name)
        if errors:
            problems.append(f"{name}: {errors} syntax error(s) after the fix:\n{text}")
        if new_mode != old_mode:
            problems.append(f"{name}: mode {oct(old_mode)} became {oct(new_mode)}")
        problems.extend(
            f"{name}: {problem}"
            for problem in _byte_problems(name, old, new, inserts=fixer_name in _INSERTING_FIXERS)
        )
        if again[name] != after[name]:
            problems.append(
                f"{name}: a second run changed the file again:\n"
                f"{after[name][0].decode()}\n---\n{again[name][0].decode()}"
            )
    if second.entries:
        problems.append(f"the second run reported fixes: {second.entries}")
    left = [e for e in second_entries if any(str(e.get("file", "")).endswith(name) for name in _FULLY_FIXED)]
    if left:
        problems.append(f"findings the first run should have fixed: {left}")
    new_diagnostics = after_diagnostics - before_diagnostics
    if new_diagnostics:
        problems.append(f"new tsc errors: {sorted(new_diagnostics)}")
    return len(first.entries), problems


@needs_treesitter
@pytest.mark.parametrize(("fixer_name", "layer"), ROUND_TRIPS)
def test_fixer_round_trip(fixer_name, layer, tmp_path, monkeypatch, capsys):
    fixed_files, problems = _round_trip(fixer_name, layer, tmp_path, _corpus_bytes(), monkeypatch)
    capsys.readouterr()
    assert not problems, "\n".join(problems)
    # The source-based fallback reports no parameters; everything else must
    # find work in the corpus, or the properties above prove nothing.
    if not (layer == "fallback" and fixer_name == "unused-params"):
        assert fixed_files, f"{fixer_name} changed nothing in the corpus"


@needs_treesitter
@pytest.mark.parametrize(("fixer_name", "layer", "name", "source"), _KNOWN_BUGS)
def test_known_fixer_bugs(fixer_name, layer, name, source, tmp_path, monkeypatch, capsys):
    fixed_files, problems = _round_trip(
        fixer_name, layer, tmp_path, {name: source.encode("utf-8")}, monkeypatch
    )
    capsys.readouterr()
    if not fixed_files:
        pytest.fail(f"{fixer_name} changed nothing in {name}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Bugs found by the round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\r", "\r\n"])
def test_byte_offset_counts_javascript_line_breaks(separator):
    """tsc ends a line at CR, U+2028 and U+2029 too; its line numbers must map back."""
    source = f"const s = 1;{separator}const t = 2;\nlet x = 1;\n".encode()
    offset = byte_offset(source, 3, 5)
    assert offset is not None and source[offset:].startswith(b"x = 1")


@needs_treesitter
def test_params_fixer_renames_the_reported_parameter_after_a_line_separator():
    """A U+2028 in a string used to shift tsc's line onto a used parameter below."""
    source = (
        "const s = 'a\u2028b';\n"
        "export function f(p: number) { return 0; }\n"
        "export function g(p: number) { return p; }\n"
    )
    # tsc ends line 1 at the U+2028, so f's `p` is at line 3, col 19.
    out, fixed, _ = prefix_unused_params(
        parse_text(source, "a.ts"), [{"name": "p", "line": 3, "col": 19}]
    )
    assert fixed
    assert out.decode("utf-8") == source.replace("f(p", "f(_p")
