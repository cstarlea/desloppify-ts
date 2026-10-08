"""Tests for the type-safety smells (``detector_types``)."""

import importlib.util

import pytest

import desloppify.languages.typescript.detectors.smells.detector_types as types_mod
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.detectors.smells.detector_types import (
    TYPE_SAFETY_SMELLS,
    _detect_type_safety,
)
from desloppify.languages.typescript.detectors.smells.helpers import _file_context
from desloppify.languages.typescript.phases_smells import _add_density

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="the type-safety smells use the syntax tree",
)


def _smells(tmp_path, content: str, name: str = "a.ts") -> dict[str, list[int]]:
    """Smell id -> matched lines, for one file."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    ctx = _file_context(str(path), content)
    counts: dict[str, list[dict]] = {smell: [] for smell in TYPE_SAFETY_SMELLS}
    _detect_type_safety(ctx, counts)
    return {smell: [m["line"] for m in matches] for smell, matches in counts.items() if matches}


@needs_treesitter
class TestTree:
    def test_every_non_null_assertion(self, tmp_path):
        found = _smells(tmp_path, "a!.b;\nf(x!);\nconst y = list[0]!;\nthis.el!.focus(); g()!.h!.i;\n")
        assert found == {"non_null_assert": [1, 2, 3, 4, 4, 4]}

    def test_definite_assignment_is_not_a_non_null_assertion(self, tmp_path):
        assert _smells(tmp_path, "let x!: number;\nclass C { y!: string }\nconst z = a !== b;\n") == {}

    def test_double_casts(self, tmp_path):
        source = (
            "const a = x as unknown as T;\n"
            "const b = (x as unknown) as T;\n"
            "const c = <T><unknown>x;\n"
            "const d = x as unknown;\n"
            "const e = x as Foo as Bar;\n"
            "const f = <T>(x as unknown);\n"
        )
        assert _smells(tmp_path, source) == {"double_cast": [1, 2, 3, 6]}

    def test_any_kinds(self, tmp_path):
        source = (
            "let a: any;\n"
            "type B = string | any;\n"
            "type C = Record<string, any>;\n"
            "const d = x as any;\n"
            "const e = x as any[];\n"
            "const f = <any>x;\n"
            "type G = keyof any;\n"
            "function h(...args: any[]): any {}\n"
            "const i = 'any: any';\n"
            "// const j: any\n"
        )
        assert _smells(tmp_path, source) == {
            "any_type": [1, 2, 3, 8, 8],
            "as_any_cast": [4, 5, 6],
        }

    def test_ts_ignore_in_line_and_block_comments(self, tmp_path):
        source = (
            "// @ts-ignore\n"
            "a();\n"
            "/* @ts-ignore */\n"
            "b();\n"
            "/** @ts-ignore */\n"
            "c();\n"
            "d(); // @ts-ignore\n"
            "e();\n"
            "/*\n"
            " * @ts-ignore\n"
            " */\n"
            "f();\n"
            "// see @ts-ignore docs\n"
            "const s = '// @ts-ignore';\n"
        )
        # TypeScript only honours a block comment whose last line holds the directive.
        assert _smells(tmp_path, source) == {"ts_ignore": [1, 3, 5, 7]}

    def test_ts_ignore_in_jsx(self, tmp_path):
        source = "const x = (\n  <div>\n    {/* @ts-ignore */}\n    <Foo bar />\n  </div>\n);\n"
        assert _smells(tmp_path, source, "a.tsx") == {"ts_ignore": [3]}

    def test_undocumented_ts_expect_error(self, tmp_path):
        source = (
            "// @ts-expect-error\n"
            "a();\n"
            "// @ts-expect-error:\n"
            "b();\n"
            "/* @ts-expect-error */\n"
            "c();\n"
            "// @ts-expect-error -- the types lag the runtime\n"
            "d();\n"
            "// @ts-expect-error: wrong overload\n"
            "e();\n"
            "// zod's inference widens this\n"
            "// @ts-expect-error\n"
            "f();\n"
            "/* the generic can't see the brand\n"
            "   @ts-expect-error */\n"
            "g();\n"
            "// @ts-expect-error\n"
            "// @ts-expect-error\n"
            "h();\n"
        )
        assert _smells(tmp_path, source) == {"ts_expect_error_undocumented": [1, 3, 5, 17, 18]}


@needs_treesitter
class TestProjectContext:
    def test_index_access_assertions_under_no_unchecked_indexed_access(self, tmp_path, set_project_root):
        source = "const a = list[0]!;\nconst b = map.get(k)!;\nconst c = row[i]!.name;\n"
        (tmp_path / "base.json").write_text('{ "compilerOptions": { "noUncheckedIndexedAccess": true } }')
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "tsconfig.json").write_text('{\n  // JSONC\n  "extends": "../base.json"\n}')
        (tmp_path / "plain").mkdir()
        (tmp_path / "plain" / "tsconfig.json").write_text('{ "compilerOptions": { "strict": true } }')
        assert _smells(tmp_path / "pkg", source) == {"non_null_assert": [2]}
        assert _smells(tmp_path / "plain", source) == {"non_null_assert": [1, 2, 3]}

    @pytest.mark.parametrize(
        "name",
        ["a.test.ts", "a.spec.tsx", "types.test-d.ts", "__tests__/a.ts", "test-d/a.ts", "test/a.ts"],
    )
    def test_expect_error_needs_no_reason_in_tests(self, tmp_path, set_project_root, name):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        source = "// @ts-expect-error\nf(1);\n// @ts-ignore\ng();\n"
        assert _smells(tmp_path, source, name) == {"ts_ignore": [3]}
        assert _smells(tmp_path, source, "src.ts") == {"ts_expect_error_undocumented": [1], "ts_ignore": [3]}


class TestRegexFallback:
    @pytest.fixture(autouse=True)
    def _no_tree(self, monkeypatch):
        monkeypatch.setattr(types_mod, "parsed_file", lambda _path: None)
        monkeypatch.setattr(types_mod, "parse_text", lambda _text, _path: None)

    def test_finds_each_smell(self, tmp_path):
        source = (
            "let a: any;\n"
            "const b = x as any;\n"
            "const c = x as unknown as T;\n"
            "const d = e!.f;\n"
            "// @ts-ignore\n"
            "// @ts-expect-error\n"
            "// @ts-expect-error -- documented\n"
            "const g = 'h: any';\n"
        )
        assert _smells(tmp_path, source) == {
            "any_type": [1],
            "as_any_cast": [2],
            "double_cast": [3],
            "non_null_assert": [4],
            "ts_ignore": [5],
            "ts_expect_error_undocumented": [6],
        }


def test_density_on_type_safety_issues(tmp_path, set_project_root):
    (tmp_path / "a.ts").write_text("let a: any;\nlet b: any;\n" + "x();\n" * 18)
    (tmp_path / "b.ts").write_text("try { f(); } catch (e) { }\n")
    entries, _ = detect_smells(tmp_path)
    by_id = {e["id"]: e for e in entries}
    assert set(by_id["any_type"]["loc"].values()) == {20}
    assert "loc" not in by_id["empty_catch"]

    issues = [
        {"file": m["file"], "summary": f"{e['count']}x {e['label']}", "detail": {"smell_id": e["id"], "count": e["count"]}}
        for e in entries
        for m in e["matches"][:1]
    ]
    _add_density(issues, entries)
    any_issue = next(i for i in issues if i["detail"]["smell_id"] == "any_type")
    assert any_issue["detail"]["density"] == 100.0
    assert any_issue["summary"].endswith(", 100 per 1,000 lines")
    catch_issue = next(i for i in issues if i["detail"]["smell_id"] == "empty_catch")
    assert "density" not in catch_issue["detail"]
