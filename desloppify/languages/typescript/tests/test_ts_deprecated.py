"""Tests for desloppify.languages.typescript.detectors.deprecated — @deprecated symbol detection."""

from pathlib import Path

import pytest

import desloppify.base.discovery.paths as paths_api_mod
import desloppify.languages.typescript.detectors.deprecated as deprecated_detector_mod
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.syntax.tree import get_parser

needs_treesitter = pytest.mark.skipif(get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar")


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root, monkeypatch):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""
    monkeypatch.setattr(paths_api_mod, "SRC_PATH", tmp_path)
    clear_resolver_cache()


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _lines(tmp_path: Path, name: str) -> list[str]:
    return (tmp_path / name).read_text().splitlines()


def _detect(path: Path) -> tuple[list[dict], int]:
    result = deprecated_detector_mod.detect_deprecated_result(path)
    return result.entries, result.population_size


# ── _extract_deprecated_symbol (regex fallback) ─────────────


class TestExtractDeprecatedSymbol:
    def test_inline_jsdoc_top_level_const(self, tmp_path):
        """Inline JSDoc @deprecated on top-level const is extracted."""

        _write(
            tmp_path,
            "old.ts",
            "/** @deprecated Use newThing instead */ export const oldThing = 1;\n",
        )
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "old.ts"),
            1,
            "/** @deprecated Use newThing instead */ export const oldThing = 1;",
        )
        assert symbol == "oldThing"
        assert kind == "top-level"

    def test_inline_jsdoc_property(self, tmp_path):
        """Inline JSDoc @deprecated on a property is extracted as property kind."""

        _write(
            tmp_path,
            "types.ts",
            (
                "interface Config {\n"
                "  /** @deprecated */ oldField?: string;\n"
                "  newField: string;\n"
                "}\n"
            ),
        )
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "types.ts"), 2, "  /** @deprecated */ oldField?: string;"
        )
        assert symbol == "oldField"
        assert kind == "property"

    def test_multiline_jsdoc_function(self, tmp_path):
        """Multi-line JSDoc @deprecated on function is extracted."""

        _write(
            tmp_path,
            "api.ts",
            (
                "/**\n"
                " * @deprecated Use newFetch instead\n"
                " */\n"
                "export function oldFetch() { return null; }\n"
            ),
        )
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "api.ts"), 2, " * @deprecated Use newFetch instead"
        )
        assert symbol == "oldFetch"
        assert kind == "top-level"

    def test_multiline_jsdoc_interface(self, tmp_path):
        """Multi-line JSDoc @deprecated on interface is extracted."""

        _write(
            tmp_path,
            "types.ts",
            (
                "/**\n"
                " * @deprecated Use NewType instead\n"
                " */\n"
                "export interface OldType {\n"
                "  field: string;\n"
                "}\n"
            ),
        )
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "types.ts"), 2, " * @deprecated Use NewType instead"
        )
        assert symbol == "OldType"
        assert kind == "top-level"

    def test_inline_comment_deprecation(self, tmp_path):
        """// @deprecated on same line as a property is extracted."""

        _write(
            tmp_path,
            "types.ts",
            ("interface Config {\n  shotImageEntryId?: string; // @deprecated\n}\n"),
        )
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "types.ts"), 2, "  shotImageEntryId?: string; // @deprecated"
        )
        assert symbol == "shotImageEntryId"
        assert kind == "property"

    def test_returns_none_for_unresolvable(self, tmp_path):
        """Returns (None, 'unknown') when the symbol cannot be determined."""

        _write(tmp_path, "weird.ts", "@deprecated\n\n\n")
        symbol, kind = deprecated_detector_mod._extract_deprecated_symbol(
            _lines(tmp_path, "weird.ts"), 1, "@deprecated"
        )
        assert symbol is None
        assert kind == "unknown"


# ── detect_deprecated ────────────────────────────────────────


class TestDetectDeprecated:
    def test_finds_deprecated_annotations(self, tmp_path):
        """detect_deprecated finds files with @deprecated JSDoc tags."""

        _write(
            tmp_path,
            "old.ts",
            (
                "/**\n"
                " * @deprecated Use newHelper instead\n"
                " */\n"
                "export function oldHelper() { return null; }\n"
            ),
        )
        entries, count = _detect(tmp_path)
        assert len(entries) >= 1
        assert entries[0]["symbol"] == "oldHelper"
        assert entries[0]["kind"] == "top-level"

    def test_deduplicates_same_symbol_in_file(self, tmp_path):
        """Same symbol with multiple @deprecated annotations in one file is deduplicated."""

        _write(
            tmp_path,
            "dupes.ts",
            (
                "/**\n"
                " * @deprecated\n"
                " * @deprecated (duplicate)\n"
                " */\n"
                "export function oldThing() {}\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        symbols = [e["symbol"] for e in entries if e["symbol"] == "oldThing"]
        assert len(symbols) <= 1

    def test_empty_directory(self, tmp_path):
        """Empty directory returns no entries."""

        entries, count = _detect(tmp_path)
        assert entries == []
        assert count == 0

    def test_file_without_deprecated(self, tmp_path):
        """Files without @deprecated produce no entries."""

        _write(tmp_path, "clean.ts", "export function activeHelper() { return 1; }\n")
        entries, _ = _detect(tmp_path)
        assert entries == []

    def test_distinguishes_top_level_and_property(self, tmp_path):
        """Entries correctly classify top-level vs property deprecations."""

        _write(
            tmp_path,
            "mixed.ts",
            (
                "/**\n"
                " * @deprecated Use new API\n"
                " */\n"
                "export function oldFunc() {}\n"
                "\n"
                "interface Config {\n"
                "  /** @deprecated */ oldProp?: string;\n"
                "}\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        kinds = {e["kind"] for e in entries}
        assert "top-level" in kinds
        assert "property" in kinds

    def test_detects_mixed_case_deprecated_markers(self, tmp_path):
        """Mixed-case @Deprecated markers should be detected."""
        _write(
            tmp_path,
            "legacy.ts",
            (
                "/**\n"
                " * @Deprecated Use newFunc\n"
                " */\n"
                "export function oldFunc() {}\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        symbols = {e["symbol"] for e in entries}
        assert "oldFunc" in symbols


class TestDeprecatedMarkersAndExports:
    def test_prose_and_identifiers_are_not_markers(self, tmp_path):
        _write(
            tmp_path,
            "flags.ts",
            (
                "export const STATUS_DEPRECATED = 'deprecated';\n"
                "// this module is not deprecated\n"
                "export function isDeprecated(x: string) { return x === 'DEPRECATED'; }\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        assert entries == []

    def test_export_and_local_use_are_recorded(self, tmp_path):
        _write(
            tmp_path,
            "api.ts",
            (
                "/** @deprecated use v2 */\n"
                "export function legacyApi() {}\n"
                "/** @deprecated */\n"
                "function helper() {}\n"
                "export function v2() { return helper(); }\n"
                "/** @deprecated */\n"
                "function dead() {}\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        by_symbol = {entry["symbol"]: entry for entry in entries}
        assert by_symbol["legacyApi"]["exported"] is True
        assert by_symbol["helper"]["exported"] is False
        assert by_symbol["helper"]["same_file_uses"] == 1
        assert by_symbol["dead"]["exported"] is False
        assert by_symbol["dead"]["same_file_uses"] == 0

    def test_declaration_files_are_skipped(self, tmp_path):
        _write(tmp_path, "types.d.ts", "/** @deprecated */\nexport declare function old(): void;\n")
        entries, count = _detect(tmp_path)
        assert entries == [] and count == 0


def _by_symbol(tmp_path: Path) -> dict[tuple[str, str], dict]:
    entries, _ = _detect(tmp_path)
    return {(Path(e["file"]).name, e["symbol"]): e for e in entries}


@needs_treesitter
class TestDeprecatedOnSyntaxTree:
    def test_jsdoc_attaches_to_the_documented_node(self, tmp_path):
        long_doc = "\n".join(f" * line {i}" for i in range(12))
        _write(
            tmp_path,
            "api.ts",
            "/**\n * @deprecated use b\n */\n// @__NO_SIDE_EFFECTS__\n\nexport async function oldAsync() {}\n"
            f"/**\n * @deprecated\n{long_doc}\n */\nexport class $Legacy {{}}\n"
            "/** @deprecated */\nexport const a = 1, b = 2;\n"
            "interface Shape {\n  keep: string; /** @deprecated */\n  gone: string;\n}\n"
            "/** @deprecated */\nexport enum E { A }\n"
            "enum F {\n  /** @deprecated */\n  Old = 1,\n  New,\n}\n"
            "class K {\n  /** @deprecated */\n  oldAsync() {}\n}\n"
            "// @deprecated is not JSDoc\nexport const notTagged = 1;\n"
            "const s = '@deprecated';\n",
        )
        found = {(e["symbol"], e["kind"]) for e in _detect(tmp_path)[0]}
        assert found == {
            ("oldAsync", "top-level"),
            ("$Legacy", "top-level"),
            ("a", "top-level"),
            ("b", "top-level"),
            ("E", "top-level"),
            ("Old", "property"),
            ("oldAsync", "property"),
        }

    def test_line_is_the_tag_line(self, tmp_path):
        _write(tmp_path, "a.ts", "/**\n * Text.\n *\n * @deprecated\n */\nexport function f() {}\n")
        assert _by_symbol(tmp_path)[("a.ts", "f")]["line"] == 4

    def test_importers_follow_the_import_graph(self, tmp_path):
        _write(tmp_path, "lib/old.ts", "/** @deprecated */\nexport function old() {}\nexport type T = 1;\nold();\n")
        _write(tmp_path, "lib/index.ts", "export * from './old';\n")
        _write(tmp_path, "direct.ts", "import { old } from './lib/old';\nold();\n")
        _write(tmp_path, "barrel.ts", "import { old as renamed } from './lib';\nrenamed();\n")
        _write(tmp_path, "ns.ts", "import * as lib from './lib';\nlib.old();\n")
        _write(tmp_path, "types.ts", "import type { old } from './lib';\nlet x: typeof old;\n")
        _write(tmp_path, "unused-ns.ts", "import * as lib from './lib';\nlet y: lib.T;\n")
        _write(tmp_path, "other.ts", "export function old() {}\nold();\n")  # same name, other symbol
        _write(tmp_path, "mention.ts", "// old() is old\nconst old = 1;\n")
        entry = _by_symbol(tmp_path)[("old.ts", "old")]
        assert entry["importers"] == 4
        assert entry["same_file_uses"] == 1
        assert entry["exported"] is True

    def test_importers_through_object_spreads(self, tmp_path):
        _write(tmp_path, "lib/old.ts", "/** @deprecated */\nexport function old() {}\n")
        _write(tmp_path, "lib/iso.ts", "/** @deprecated */\nexport function legacy() {}\n")
        _write(
            tmp_path,
            "local.ts",
            "import * as _schemas from './lib/old';\nimport * as _iso from './lib/iso';\n"
            "const z = { ..._schemas, iso: _iso, other: 1 };\nz.old();\nz.iso.legacy();\n",
        )
        _write(tmp_path, "unrelated.ts", "const z = { other: 1 };\nz.old();\n")
        found = _by_symbol(tmp_path)
        assert found[("old.ts", "old")]["importers"] == 1
        assert found[("iso.ts", "legacy")]["importers"] == 1

    def test_deprecated_reexport_alias(self, tmp_path):
        _write(tmp_path, "impl.ts", "export function current() {}\nexport type Shape = {};\n")
        _write(
            tmp_path,
            "index.ts",
            "export {\n  current,\n  /** @deprecated use current */\n  current as legacy,\n} from './impl';\n"
            "export type {\n  /** @deprecated */\n  Shape,\n} from './impl';\n",
        )
        _write(tmp_path, "uses-legacy.ts", "import { legacy } from './index';\nlegacy();\n")
        _write(tmp_path, "uses-current.ts", "import { current } from './index';\ncurrent();\n")
        found = _by_symbol(tmp_path)
        assert set(found) == {("index.ts", "legacy"), ("index.ts", "Shape")}
        assert found[("index.ts", "legacy")]["importers"] == 1
        assert found[("index.ts", "Shape")]["importers"] == 0
        assert found[("index.ts", "Shape")]["exported"] is True

    def test_single_deprecated_overload_is_not_the_symbol(self, tmp_path):
        _write(
            tmp_path,
            "over.ts",
            "/** @deprecated pass options */\nexport function f(a: string): void;\n"
            "export function f(o: object): void;\nexport function f(x: unknown) {}\n"
            "/** @deprecated */\nexport function g(a: string): void;\n"
            "/** @deprecated */\nexport function g(o: object): void;\nexport function g(x: unknown) {}\n",
        )
        found = _by_symbol(tmp_path)
        assert found[("over.ts", "f")]["kind"] == "overload"
        assert found[("over.ts", "g")]["kind"] == "top-level"

    def test_member_named_like_a_top_level_symbol(self, tmp_path):
        _write(
            tmp_path,
            "s.ts",
            "interface S {\n  /** @deprecated */\n  cuid(): this;\n}\n/** @deprecated */\nexport function cuid() {}\n",
        )
        kinds = sorted(e["kind"] for e in _detect(tmp_path)[0])
        assert kinds == ["property", "top-level"]

    def test_commonjs_export_is_not_unused(self, tmp_path):
        _write(tmp_path, "cjs.js", "/** @deprecated */\nfunction old() {}\nmodule.exports = { old };\n")
        assert _by_symbol(tmp_path)[("cjs.js", "old")]["exported"] is True

    def test_each_file_is_parsed_a_bounded_number_of_times(self, tmp_path, monkeypatch):
        decls = "".join(f"/** @deprecated */\nexport function f{i}() {{}}\n" for i in range(10))
        _write(tmp_path, "many.ts", decls)
        _write(tmp_path, "user.ts", "import { f1, f2 } from './many';\nf1(); f2();\n")
        calls: dict[str, int] = {}
        real = deprecated_detector_mod.parsed_file

        def counting(path):
            calls[Path(path).name] = calls.get(Path(path).name, 0) + 1
            return real(path)

        monkeypatch.setattr(deprecated_detector_mod, "parsed_file", counting)
        entries, _ = _detect(tmp_path)
        assert len(entries) == 10
        assert max(calls.values()) <= 2
