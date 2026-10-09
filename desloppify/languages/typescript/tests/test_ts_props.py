"""Tests for desloppify.languages.typescript.detectors.props — prop interface bloat detection."""

from pathlib import Path

import pytest

from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.detectors.props import detect_prop_interface_bloat
from desloppify.languages.typescript.syntax.tree import get_parser

needs_treesitter = pytest.mark.skipif(
    get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar"
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""
    clear_resolver_cache()


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ── detect_prop_interface_bloat ──────────────────────────────


class TestDetectPropInterfaceBloat:
    def test_detects_bloated_props_interface(self, tmp_path):
        """Interface with >14 props is flagged."""

        props = "\n".join(f"  prop{i}: string;" for i in range(20))
        _write(
            tmp_path, "Component.tsx", (f"interface ComponentProps {{\n{props}\n}}\n")
        )
        entries, total = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["interface"] == "ComponentProps"
        assert entries[0]["prop_count"] == 20
        assert entries[0]["kind"] == "props"
        assert total >= 1

    def test_small_interface_not_flagged(self, tmp_path):
        """Interface with <=14 props is not flagged."""

        props = "\n".join(f"  prop{i}: string;" for i in range(5))
        _write(tmp_path, "Component.tsx", (f"interface ButtonProps {{\n{props}\n}}\n"))
        entries, total = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 0
        assert total >= 1

    def test_context_interface_detected(self, tmp_path):
        """Context-related interfaces are also checked."""

        props = "\n".join(f"  field{i}: string;" for i in range(16))
        _write(
            tmp_path, "context.tsx", (f"interface AppContextValue {{\n{props}\n}}\n")
        )
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "context"

    def test_state_interface_detected(self, tmp_path):
        """State-related interfaces are also checked."""

        props = "\n".join(f"  field{i}: number;" for i in range(16))
        _write(tmp_path, "store.tsx", (f"interface EditorState {{\n{props}\n}}\n"))
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "state"

    def test_non_props_interface_ignored(self, tmp_path):
        """Interfaces not matching Props/Context/State suffixes are ignored."""

        props = "\n".join(f"  field{i}: string;" for i in range(20))
        _write(tmp_path, "types.tsx", (f"interface UserData {{\n{props}\n}}\n"))
        entries, total = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 0

    def test_type_alias_with_props_suffix(self, tmp_path):
        """Type aliases with Props suffix are also detected."""

        props = "\n".join(f"  prop{i}: string;" for i in range(16))
        _write(tmp_path, "Component.tsx", (f"type WidgetProps = {{\n{props}\n}};\n"))
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["interface"] == "WidgetProps"

    def test_export_interface_detected(self, tmp_path):
        """Exported interfaces are also detected."""

        props = "\n".join(f"  prop{i}: string;" for i in range(16))
        _write(
            tmp_path, "Component.tsx", (f"export interface CardProps {{\n{props}\n}}\n")
        )
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["interface"] == "CardProps"

    def test_empty_directory(self, tmp_path):
        """Empty directory returns no entries."""

        entries, total = detect_prop_interface_bloat(tmp_path)
        assert entries == []
        assert total == 0

    def test_comments_not_counted_as_props(self, tmp_path):
        """Comment lines inside interfaces are not counted as props."""

        real_props = "\n".join(f"  prop{i}: string;" for i in range(10))
        comments = "\n".join(f"  // comment line {i}" for i in range(10))
        _write(
            tmp_path,
            "Component.tsx",
            (f"interface TestProps {{\n{real_props}\n{comments}\n}}\n"),
        )
        entries, _ = detect_prop_interface_bloat(tmp_path)
        # Should only count the 10 real props, not the 10 comments
        assert len(entries) == 0

    def test_results_sorted_by_prop_count_descending(self, tmp_path):
        """Results sorted by prop_count in descending order."""

        props_15 = "\n".join(f"  p{i}: string;" for i in range(15))
        props_20 = "\n".join(f"  p{i}: string;" for i in range(20))
        _write(tmp_path, "small.tsx", f"interface SmallProps {{\n{props_15}\n}}\n")
        _write(tmp_path, "big.tsx", f"interface BigProps {{\n{props_20}\n}}\n")

        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 2
        assert entries[0]["prop_count"] >= entries[1]["prop_count"]

    def test_context_type_suffix(self, tmp_path):
        """Interfaces with ContextType suffix are detected."""

        props = "\n".join(f"  field{i}: string;" for i in range(16))
        _write(tmp_path, "ctx.tsx", (f"interface MyContextType {{\n{props}\n}}\n"))
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "context"

    def test_state_value_suffix(self, tmp_path):
        """Interfaces with StateValue suffix are detected."""

        props = "\n".join(f"  field{i}: string;" for i in range(16))
        _write(tmp_path, "store.tsx", (f"interface FormStateValue {{\n{props}\n}}\n"))
        entries, _ = detect_prop_interface_bloat(tmp_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "state"

    def test_names_match_on_word_boundaries(self, tmp_path):
        """``Statement`` and ``Contextual`` aren't State/Context; bare ``Props`` is."""

        props = "\n".join(f"  p{i}: string;" for i in range(16))
        _write(
            tmp_path,
            "names.ts",
            f"interface Statement {{\n{props}\n}}\n"
            f"interface ContextualInfo {{\n{props}\n}}\n"
            f"interface Props {{\n{props}\n}}\n"
            f"interface UIState {{\n{props}\n}}\n",
        )
        entries, total = detect_prop_interface_bloat(tmp_path)
        assert {e["interface"]: e["kind"] for e in entries} == {
            "Props": "props",
            "UIState": "state",
        }
        assert total == 2


def _props(names, indent: str = "  ") -> str:
    return "\n".join(f"{indent}{n}: string;" for n in names)


@needs_treesitter
class TestPropsOnSyntaxTree:
    def test_counts_properties_not_lines(self, tmp_path):
        """Multi-line members, nested object types and comments count as written."""

        members = "\n".join(
            f"  /** doc {i} */\n  p{i}: {{\n    nested: string;\n    other: number;\n  }};"
            for i in range(8)
        )
        _write(
            tmp_path,
            "a.ts",
            f"interface WideProps {{\n{members}\n  onA(): void;\n  onA(x: number): void;\n}}\n",
        )
        entries, _ = detect_prop_interface_bloat(tmp_path, threshold=5)
        assert entries[0]["prop_count"] == 9

    def test_extends_generics_and_intersections(self, tmp_path):
        _write(
            tmp_path,
            "base.ts",
            f"export interface BaseFields {{\n{_props(f'b{i}' for i in range(8))}\n}}\n",
        )
        _write(
            tmp_path,
            "Card.tsx",
            "import type { BaseFields } from './base';\n"
            "interface Extra<T> { x: T; y: T }\n"
            "export interface CardProps<T> extends BaseFields, Extra<T>, React.HTMLAttributes<T> {\n"
            f"{_props(f'c{i}' for i in range(6))}\n  b0: string;\n}}\n"
            "type PanelProps<T> = Omit<BaseFields, 'b0' | 'b1'> & Partial<Extra<T>> & T & {\n"
            f"{_props(['z'])}\n}};\n"
            "type PickedProps = Pick<BaseFields, 'b0'> & ({ a: 1 } | { b: 1; c: 1 });\n",
        )
        entries, total = detect_prop_interface_bloat(tmp_path, threshold=0)
        counts = {e["interface"]: e["prop_count"] for e in entries}
        assert counts == {
            "CardProps": 8 + 2 + 6,
            "PanelProps": 6 + 2 + 1,
            "PickedProps": 1 + 2,
        }
        assert total == 3

    def test_bases_found_through_barrels(self, tmp_path):
        _write(
            tmp_path,
            "types/base.ts",
            f"export interface Base {{\n{_props(['a', 'b'])}\n}}\n",
        )
        _write(
            tmp_path,
            "types/other.ts",
            f"export type Other = {{\n{_props(['c'])}\n}};\n",
        )
        _write(
            tmp_path,
            "types/index.ts",
            "export * from './base';\nexport { Other as Renamed } from './other';\n",
        )
        _write(
            tmp_path,
            "Form.tsx",
            "import { Base, Renamed } from './types';\nexport type FormProps = Base & Renamed & { d: string };\n",
        )
        entries, _ = detect_prop_interface_bloat(tmp_path, threshold=0)
        assert {e["interface"]: e["prop_count"] for e in entries} == {"FormProps": 4}

    def test_same_name_interfaces_merge(self, tmp_path):
        _write(
            tmp_path,
            "merged.ts",
            f"interface MenuState {{\n{_props(['a', 'b'])}\n}}\ninterface MenuState {{\n{_props(['b', 'c'])}\n}}\n",
        )
        entries, total = detect_prop_interface_bloat(tmp_path, threshold=0)
        assert [(e["interface"], e["prop_count"]) for e in entries] == [
            ("MenuState", 3)
        ]
        assert total == 1

    def test_renames_and_property_less_types_are_skipped(self, tmp_path):
        _write(
            tmp_path,
            "ctx.tsx",
            f"interface AppContextValue {{\n{_props(f'p{i}' for i in range(16))}\n}}\n"
            "type ProviderContext = AppContextValue;\n"
            "type LoadState = 'idle' | 'busy';\n"
            "type InputProps = React.InputHTMLAttributes<HTMLInputElement>;\n",
        )
        entries, total = detect_prop_interface_bloat(tmp_path)
        assert [e["interface"] for e in entries] == ["AppContextValue"]
        assert total == 1

    def test_circular_extends_terminates(self, tmp_path):
        _write(
            tmp_path,
            "loop.ts",
            "interface AProps extends BProps { a: string }\ninterface BProps extends AProps { b: string }\n",
        )
        entries, _ = detect_prop_interface_bloat(tmp_path, threshold=0)
        assert {e["interface"]: e["prop_count"] for e in entries} == {
            "AProps": 2,
            "BProps": 2,
        }
