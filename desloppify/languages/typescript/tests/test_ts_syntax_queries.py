"""Typed syntax-tree queries and the parse-once entry point (roadmap 2.1)."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify.languages.typescript.syntax.tree as tree_mod
from desloppify.languages._framework.treesitter import (
    TYPESCRIPT_SPEC,
    disable_parse_cache,
    enable_parse_cache,
)
from desloppify.languages._framework.treesitter.cohesion import (
    detect_responsibility_cohesion,
)
from desloppify.languages.typescript.detectors.deps.imports import ImportExtractor
from desloppify.languages.typescript.detectors.facade import is_ts_facade
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.syntax.queries import (
    calls,
    classes,
    definitions,
    descendants,
    exports,
    function_info,
    functions,
    imports,
    jsx_elements,
    module_statements,
    type_declarations,
)
from desloppify.languages.typescript.syntax.tree import parse_text, parsed_file

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the syntax queries need tree-sitter",
)


def _parse(source: str, path: str = "a.ts"):
    parsed = parse_text(source, path)
    assert parsed is not None
    return parsed


def _functions(source: str, path: str = "a.ts", **kwargs):
    return {f.name: f for f in functions(_parse(source, path), **kwargs)}


# ── Functions ───────────────────────────────────────────────


def test_function_declaration_flags_and_params():
    fn = _functions(
        "export async function* load<T>(a: T, b?: number, c = 2, ...rest: string[]) {\n  yield a;\n}\n"
    )["load"]
    assert (fn.kind, fn.is_async, fn.is_generator, fn.exported, fn.default_export) == (
        "declaration", True, True, True, False
    )
    assert [(p.name, p.type, p.optional, p.rest, p.default) for p in fn.params] == [
        ("a", "T", False, False, None),
        ("b", "number", True, False, None),
        ("c", None, False, False, "2"),
        ("rest", "string[]", False, True, None),
    ]
    assert (fn.span.start_line, fn.span.end_line) == (1, 3)
    assert fn.body is not None and (fn.body.start_line, fn.body.end_line) == (1, 3)
    assert not fn.expression_body


def test_default_export_functions():
    # The grammar reads an anonymous ``export default function`` as an expression.
    fns = functions(_parse("export default function () {}\nexport default async () => 1;\n"))
    assert [(f.name, f.kind, f.exported, f.default_export, f.is_async) for f in fns] == [
        (None, "expression", True, True, False),
        (None, "arrow", True, True, True),
    ]


def test_named_default_and_local_exports():
    fns = _functions("function a() {}\nconst b = () => {};\nfunction c() {}\nexport { a };\nexport default b;\n")
    assert (fns["a"].exported, fns["a"].default_export) == (True, False)
    assert (fns["b"].exported, fns["b"].default_export) == (True, True)
    assert not fns["c"].exported


def test_multi_line_params():
    fn = _functions("function f(\n  a: string,\n  { b, c }: Opts,\n  [d]: number[],\n) {\n  return a;\n}\n")["f"]
    assert [p.name for p in fn.params] == ["a", "{ b, c }", "[d]"]
    assert fn.params[1].type == "Opts"
    assert (fn.span.start_line, fn.span.end_line) == (1, 7)


def test_concise_and_block_arrows():
    fns = _functions("const one = x => x * 2;\nconst two = async (a, b) => ({ a, b });\nconst three = () => {\n};\n")
    assert fns["one"].expression_body and [p.name for p in fns["one"].params] == ["x"]
    assert fns["two"].expression_body and fns["two"].is_async
    assert not fns["three"].expression_body
    assert fns["one"].body is not None and fns["one"].body.start_line == 1


def test_bound_names_for_anonymous_functions():
    source = (
        "const o = { p: function () {}, 'q-r': () => 1, s() {} };\n"
        "module.exports.t = function () {};\n"
        "items.map((i) => i);\n"
        "const u = function named() {};\n"
    )
    names = [f.name for f in functions(_parse(source))]
    assert names == ["p", "q-r", "s", "module.exports.t", None, "named"]


def test_methods_and_class_fields():
    source = (
        "class A {\n"
        "  constructor(public x: number) {}\n"
        "  get v() { return 1; }\n"
        "  set v(n) {}\n"
        "  static async *gen() {}\n"
        "  @dec override async run() {}\n"
        "  handler = () => {};\n"
        "  get() {}\n"
        "}\n"
    )
    fns = functions(_parse(source))
    assert [(f.name, f.kind, f.accessor, f.is_static, f.is_async, f.is_generator) for f in fns] == [
        ("constructor", "method", None, False, False, False),
        ("v", "method", "get", False, False, False),
        ("v", "method", "set", False, False, False),
        ("gen", "method", None, True, True, True),
        ("run", "method", None, False, True, False),
        ("handler", "arrow", None, False, False, False),
        ("get", "method", None, False, False, False),
    ]
    assert {f.owner for f in fns} == {"A"}
    assert fns[0].params[0].name == "x"


def test_overloads_are_signatures():
    source = (
        "export function f(a: string): void;\n"
        "export function f(a: number): void;\n"
        "export function f(a: any) {}\n"
        "abstract class C {\n  m(): void;\n  m(x?: any) {}\n  abstract z(): void;\n}\n"
        "interface I { n(): void }\n"
    )
    parsed = _parse(source)
    assert [(f.name, f.kind) for f in functions(parsed)] == [("f", "declaration"), ("m", "method")]
    every = functions(parsed, signatures=True)
    assert [(f.name, f.kind, f.body is None) for f in every] == [
        ("f", "signature", True),
        ("f", "signature", True),
        ("f", "declaration", False),
        ("m", "signature", True),
        ("m", "method", False),
        ("z", "signature", True),
    ]
    assert all(f.exported for f in every[:3])
    assert every[3].owner == "C"


def test_nested_functions_in_source_order():
    names = [f.name for f in functions(_parse("function outer() {\n  function inner() {}\n  const x = () => 1;\n}\n"))]
    assert names == ["outer", "inner", "x"]


def _definitions(source: str, path: str = "a.ts"):
    return [(d.name, d.line, d.object_member) for d in definitions(_parse(source, path))]


def test_definitions_name_object_members_by_their_path():
    source = (
        "const api = {\n"
        "  get() {},\n"
        "  post: async () => 1,\n"
        "  put: function () {},\n"
        "  nested: { deep() {} },\n"
        "  'kebab-key': () => 1,\n"
        "  [Symbol.iterator]() {},\n"
        "  get size() { return 1; },\n"
        "} as const;\n"
        "module.exports = { handler() {} };\n"
        "const cfg = ({ load() {} }) satisfies Config;\n"
        "export default { fetch() {} };\n"
        "register({ onError(err) {} });\n"
        "class C { m() {} }\n"
    )
    assert _definitions(source) == [
        ("api.get", 2, True),
        ("api.post", 3, True),
        ("api.put", 4, True),
        ("api.nested.deep", 5, True),
        ("api.kebab-key", 6, True),
        ("api[Symbol.iterator]", 7, True),
        ("api.size", 8, True),
        ("module.exports.handler", 10, True),
        ("cfg.load", 11, True),
        ("default.fetch", 12, True),
        ("onError", 13, True),
        ("C.m", 14, False),
    ]


def test_definitions_include_anonymous_default_exports():
    assert _definitions("export default function () {}\n") == [("default", 1, False)]
    assert _definitions("export default async () => {};\n") == [("default", 1, False)]
    defs = definitions(_parse("export default function () {}\n"))
    assert defs[0].function.default_export


def test_definitions_leave_out_callbacks_and_object_values():
    source = "items.map((x) => x);\nconst o = { a: 1, b: [() => 1], c: f(() => 2) };\n"
    assert _definitions(source) == []


def test_function_info_for_a_callback():
    parsed = _parse("useEffect(async function () {}, []);\nuseEffect(1);\n", "a.tsx")
    first, second = calls(parsed, {"useEffect"})
    info = function_info(parsed, first.arguments[0])
    assert info is not None and (info.kind, info.name, info.is_async) == ("expression", None, True)
    assert function_info(parsed, second.arguments[0]) is None


# ── Classes ─────────────────────────────────────────────────


def test_class_heritage_and_members():
    source = (
        "export abstract class C<T> extends Base<T> implements I, J<T> {\n"
        "  static #count = 1;\n"
        "  private readonly name?: string;\n"
        "  protected constructor() { super(); }\n"
        "  get size() { return 1; }\n"
        "  set size(v) {}\n"
        "  m(): void;\n"
        "  m() {}\n"
        "  abstract z(): void;\n"
        "  [key: string]: unknown;\n"
        "  static {}\n"
        "}\n"
    )
    (cls,) = classes(_parse(source))
    assert (cls.name, cls.kind, cls.is_abstract, cls.exported, cls.default_export) == (
        "C", "declaration", True, True, False
    )
    assert cls.extends == "Base<T>"
    assert cls.implements == ("I", "J<T>")
    members = [
        (m.name, m.kind, m.is_static, m.is_abstract, m.is_readonly, m.is_optional, m.accessibility)
        for m in cls.members
    ]
    assert members == [
        ("#count", "field", True, False, False, False, None),
        ("name", "field", False, False, True, True, "private"),
        ("constructor", "constructor", False, False, False, False, "protected"),
        ("size", "getter", False, False, False, False, None),
        ("size", "setter", False, False, False, False, None),
        ("m", "signature", False, False, False, False, None),
        ("m", "method", False, False, False, False, None),
        ("z", "signature", False, True, False, False, None),
        ("key", "index_signature", False, False, False, False, None),
        (None, "static_block", True, False, False, False, None),
    ]
    assert [m.name for m in cls.methods] == ["constructor", "size", "size", "m"]
    assert (cls.span.start_line, cls.span.end_line) == (1, 12)


def test_class_expressions_and_default_export():
    found = classes(_parse("const A = class extends B {};\nexport default class {}\nclass D {}\nexport { D };\n"))
    assert [(c.name, c.kind, c.extends, c.exported, c.default_export) for c in found] == [
        ("A", "expression", "B", False, False),
        (None, "expression", None, True, True),
        ("D", "declaration", None, True, False),
    ]


# ── Imports ─────────────────────────────────────────────────


def test_every_import_form():
    source = (
        "import D, { a, b as c, type T } from './m';\n"
        'import * as ns from "./n";\n'
        "import type { U } from './u';\n"
        "import './side.css';\n"
        "import x = require('x');\n"
        "import j from './j.json' with { type: 'json' };\n"
        "import {} from './empty';\n"
        "declare module 'mod' {\n  import y from 'y';\n}\n"
    )
    found = imports(_parse(source))
    summary = [
        (i.source, i.kind, i.type_only, [(b.imported, b.local, b.type_only) for b in i.bindings])
        for i in found
    ]
    assert summary == [
        ("./m", "static", False, [("default", "D", False), ("a", "a", False), ("b", "c", False), ("T", "T", True)]),
        ("./n", "static", False, [("*", "ns", False)]),
        ("./u", "static", True, [("U", "U", False)]),
        ("./side.css", "side_effect", False, []),
        ("x", "require", False, [("=", "x", False)]),
        ("./j.json", "static", False, [("default", "j", False)]),
        ("./empty", "static", False, []),
        ("y", "static", False, [("default", "y", False)]),
    ]
    assert [i.line for i in found] == [1, 2, 3, 4, 5, 6, 7, 9]
    assert not any(i.has_error for i in found)


def test_import_alias_is_not_an_import():
    assert imports(_parse("namespace N { export const a = 1; }\nimport A = N.a;\n")) == []


# ── Exports ─────────────────────────────────────────────────


def _exports(source: str):
    return [
        (e.kind, e.source, e.type_only, e.star, e.is_default, [(b.name, b.exported, b.type_only) for b in e.bindings])
        for e in exports(_parse(source))
    ]


def test_reexport_forms():
    source = (
        "export * from './s';\n"
        "export * as nn from './nn';\n"
        "export type { V } from './v';\n"
        "export type * from './w';\n"
        "export { type T as TT, b, default } from './m';\n"
        "export {\n  a,\n  c as d,\n} from './multi';\n"
    )
    assert _exports(source) == [
        ("reexport", "./s", False, True, False, []),
        ("reexport", "./nn", False, True, False, [("*", "nn", False)]),
        ("reexport", "./v", True, False, False, [("V", "V", True)]),
        ("reexport", "./w", True, True, False, []),
        ("reexport", "./m", False, False, False, [("T", "TT", True), ("b", "b", False), ("default", "default", False)]),
        ("reexport", "./multi", False, False, False, [("a", "a", False), ("c", "d", False)]),
    ]
    assert not any(e.has_error for e in exports(_parse(source)))


def test_local_and_declaration_exports():
    source = (
        "export { a, c as e };\n"
        "export type { T };\n"
        "export default D;\n"
        "export default class {}\n"
        "export default function f() {}\n"
        "export const k = 1, { l, m: n } = o;\n"
        "export interface Q {}\n"
        "export type R = string;\n"
        "export enum E { A }\n"
        "export declare function g(): void;\n"
        "export namespace NS { export const inner = 1; }\n"
        "export = x;\n"
        "export as namespace Lib;\n"
    )
    assert _exports(source) == [
        ("named", None, False, False, False, [("a", "a", False), ("c", "e", False)]),
        ("named", None, True, False, False, [("T", "T", True)]),
        ("default", None, False, False, True, [("D", "default", False)]),
        ("default", None, False, False, True, []),
        ("declaration", None, False, False, True, [("f", "default", False)]),
        ("declaration", None, False, False, False, [("k", "k", False), ("l", "l", False), ("n", "n", False)]),
        ("declaration", None, False, False, False, [("Q", "Q", False)]),
        ("declaration", None, False, False, False, [("R", "R", False)]),
        ("declaration", None, False, False, False, [("E", "E", False)]),
        ("declaration", None, False, False, False, [("g", "g", False)]),
        ("declaration", None, False, False, False, [("NS", "NS", False)]),
        ("declaration", None, False, False, False, [("inner", "inner", False)]),
        ("assignment", None, False, False, False, []),
        ("namespace", None, False, False, False, []),
    ]


def test_broken_export_has_error():
    (first, second) = exports(_parse("export * from './x'\nexport { a from './y';\n"))
    assert not first.has_error
    assert second.has_error


def test_module_statements_enter_declared_modules():
    parsed = _parse("declare global { export function g(): void; }\nconst a = 1;\n")
    assert [n.type for n in module_statements(parsed)] == ["ambient_declaration", "export_statement", "lexical_declaration"]


# ── JSX ─────────────────────────────────────────────────────


def test_jsx_elements_and_attributes():
    source = (
        "export const App = () => (\n"
        "  <Layout.Main title=\"t\" {...rest} count={2} disabled on:click={f}>\n"
        "    <Item key={i} />\n"
        "    <>text</>\n"
        "  </Layout.Main>\n"
        ");\n"
    )
    found = jsx_elements(_parse(source, "a.tsx"))
    assert [(e.name, e.self_closing, e.is_fragment, e.line) for e in found] == [
        ("Layout.Main", False, False, 2),
        ("Item", True, False, 3),
        ("", False, True, 4),
    ]
    main = found[0]
    assert [(a.name, a.value, a.spread) for a in main.attributes] == [
        ("title", '"t"', False),
        (None, "rest", True),
        ("count", "{2}", False),
        ("disabled", None, False),
        ("on:click", "{f}", False),
    ]
    assert main.attribute("count") is not None and main.attribute("missing") is None
    assert (main.span.start_line, main.span.end_line) == (2, 5)


def test_jsx_needs_the_tsx_grammar():
    assert jsx_elements(_parse("const a = <T>value;\n", "a.ts")) == []
    assert [e.name for e in jsx_elements(_parse("const a = <div />;\n", "a.js"))] == ["div"]


# ── Calls and walks ─────────────────────────────────────────


def test_calls_filter_by_callee():
    parsed = _parse("useEffect(() => {}, /* deps */ []);\nReact.useEffect(f);\nother();\n", "a.tsx")
    found = calls(parsed, {"useEffect", "React.useEffect"})
    assert [(c.callee, c.line, len(c.arguments)) for c in found] == [
        ("useEffect", 1, 2),
        ("React.useEffect", 2, 1),
    ]
    assert len(calls(parsed)) == 3


def test_descendants_in_source_order():
    parsed = _parse("a(); b(c());\n")
    assert [parsed.text(n) for n in descendants(parsed.root, {"call_expression"})] == ["a()", "b(c())", "c()"]


# ── Type declarations ───────────────────────────────────────


def test_type_declarations():
    parsed = _parse(
        "export interface A<T, U = T> extends B<T>, C { a: T }\n"
        "type D = A<string> & { d: 1 };\n"
        "namespace N { interface E {} }\n"
    )
    found = {d.name: d for d in type_declarations(parsed)}
    assert list(found) == ["A", "D", "E"]
    a, d = found["A"], found["D"]
    assert (a.kind, a.type_parameters, a.exported, a.line) == ("interface", ("T", "U"), True, 1)
    assert [parsed.text(n) for n in a.extends] == ["B<T>", "C"]
    assert a.value.type == "interface_body"
    assert (d.kind, d.exported, d.extends, d.value.type) == ("alias", False, (), "intersection_type")
    assert a.span.start_byte == 0  # from ``export``


# ── Parse once per scan ─────────────────────────────────────


@pytest.fixture
def parse_counter(monkeypatch):
    counts: dict[str, int] = {}
    real = tree_mod.get_parser

    class _Counting:
        def __init__(self, grammar, parser):
            self.grammar, self.parser = grammar, parser

        def parse(self, source):
            counts[self.grammar] = counts.get(self.grammar, 0) + 1
            return self.parser.parse(source)

    def counting(grammar):
        parser = real(grammar)
        return None if parser is None else _Counting(grammar, parser)

    monkeypatch.setattr(tree_mod, "get_parser", counting)
    return counts


def test_parsed_file_parses_once_per_scan(tmp_path, set_project_root, parse_counter):
    source = (
        "import { a } from './a';\n"
        "export { a };\n"
        "export * from './b';\n"
    )
    target = tmp_path / "index.tsx"
    target.write_text(source)
    enable_parse_cache()
    try:
        first = parsed_file(target)
        assert first is not None and first.source == source.encode()
        assert parsed_file("index.tsx") is not None  # relative to the project root
        assert is_ts_facade(str(target)) is not None
        detect_smells(tmp_path)
        assert parse_counter == {"tsx": 1}
        assert imports(first)[0].source == "./a"

        # A new scan sees the file's new contents.
        target.write_text("useEffect(() => {}, []);\n")
        enable_parse_cache()
        entries, _ = detect_smells(tmp_path)
        assert any(e["id"] == "dead_useeffect" for e in entries)
        assert parsed_file(target).source.startswith(b"useEffect")
        assert parse_counter == {"tsx": 2}
    finally:
        disable_parse_cache()


def test_grammar_follows_extension(tmp_path, set_project_root, parse_counter):
    (tmp_path / "a.ts").write_text("const x = <T>y;\n")
    (tmp_path / "b.tsx").write_text("const x = <div />;\n")
    enable_parse_cache()
    try:
        for _ in range(2):
            assert not parsed_file(tmp_path / "a.ts").root.has_error
            assert not parsed_file(tmp_path / "b.tsx").root.has_error
    finally:
        disable_parse_cache()
    assert parse_counter == {"typescript": 1, "tsx": 1}


def test_parsed_file_without_a_scan_reparses(tmp_path, set_project_root, parse_counter):
    target = tmp_path / "a.ts"
    target.write_text("let a = 1;\n")
    parsed_file(target)
    parsed_file(target)
    assert parse_counter == {"typescript": 2}


def test_parsed_file_missing_or_without_tree_sitter(tmp_path, set_project_root, monkeypatch):
    assert parsed_file(tmp_path / "missing.ts") is None
    (tmp_path / "a.ts").write_text("let a = 1;\n")
    monkeypatch.setattr(tree_mod, "get_parser", lambda grammar: None)
    assert parsed_file(tmp_path / "a.ts") is None


def test_cohesion_and_imports_share_the_parse(tmp_path, set_project_root, parse_counter):
    # cohesion gets project-relative paths, deps/imports absolute ones; a
    # ``.ts`` file is parsed once, with the typescript grammar, for both.
    (tmp_path / "a.ts").write_text("import { b } from './b';\nconst x = <T>b;\n")
    enable_parse_cache()
    try:
        _entries, checked = detect_responsibility_cohesion(["a.ts"], TYPESCRIPT_SPEC, min_loc=1)
        refs = ImportExtractor().extract(str(tmp_path / "a.ts"))
        assert not parsed_file(tmp_path / "a.ts").root.has_error
    finally:
        disable_parse_cache()
    assert checked == 1
    assert [r.specifier for r in refs] == ["./b"]
    assert parse_counter == {"typescript": 1}
