"""Tests for the layer-based coupling detectors."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.engine.detectors.coupling import (
    Layer,
    detect_boundary_candidates,
    detect_layer_violations,
)

LAYERS = (
    Layer("app", ("src/app",)),
    Layer("features", ("src/features",), sliced=True),
    Layer("entities", ("src/entities",), sliced=True, cross_import_dir="@x"),
    Layer("shared", ("src/shared", "src/lib")),
)


@pytest.fixture(autouse=True)
def _root(set_project_root):
    """Layer paths are relative to the project root (tmp_path)."""


def _graph(root: Path, edges: dict[str, list[str]]) -> dict:
    graph: dict[str, dict] = {}
    for source, targets in edges.items():
        for name in (source, *targets):
            graph.setdefault(str(root / name), {"imports": set(), "importers": set()})
        graph[str(root / source)]["imports"] |= {str(root / t) for t in targets}
        for target in targets:
            graph[str(root / target)]["importers"].add(str(root / source))
    for node in graph.values():
        node["importer_count"] = len(node["importers"])
    return graph


def _write(root: Path, name: str, lines: int = 10) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"// {i}" for i in range(lines)))


class TestLayerViolations:
    def test_upward_import_is_a_violation(self, tmp_path):
        graph = _graph(tmp_path, {"src/shared/format.ts": ["src/features/cart/price.ts"]})
        entries, counts = detect_layer_violations(tmp_path, graph, LAYERS)
        assert [e["kind"] for e in entries] == ["upward"]
        entry = entries[0]
        assert entry["layer"] == "shared" and entry["target_layer"] == "features"
        assert entry["slice"] == "cart"
        assert entry["target"] == "src/features/cart/price.ts"
        assert entry["direction"] == "shared→features"
        assert counts.violating_edges == 1 and counts.eligible_edges == 1

    def test_downward_and_same_layer_imports_are_fine(self, tmp_path):
        graph = _graph(
            tmp_path,
            {
                "src/app/main.ts": ["src/features/cart/index.ts", "src/lib/http.ts"],
                "src/features/cart/index.ts": ["src/features/cart/model.ts", "src/shared/ui.ts"],
                "src/shared/ui.ts": ["src/lib/http.ts"],
            },
        )
        entries, counts = detect_layer_violations(tmp_path, graph, LAYERS)
        assert entries == []
        assert counts.eligible_edges == 5

    def test_cross_slice_import(self, tmp_path):
        graph = _graph(tmp_path, {"src/features/cart/add.ts": ["src/features/auth/session.ts"]})
        entries, _ = detect_layer_violations(tmp_path, graph, LAYERS)
        assert len(entries) == 1
        assert entries[0]["kind"] == "cross_slice"
        assert (entries[0]["source_slice"], entries[0]["target_slice"]) == ("cart", "auth")

    def test_cross_import_dir_is_allowed(self, tmp_path):
        graph = _graph(
            tmp_path,
            {
                "src/entities/order/model.ts": [
                    "src/entities/user/@x/order.ts",
                    "src/entities/user/model.ts",
                ]
            },
        )
        entries, _ = detect_layer_violations(tmp_path, graph, LAYERS)
        assert [e["target"] for e in entries] == ["src/entities/user/model.ts"]

    def test_files_outside_layers_are_ignored(self, tmp_path):
        graph = _graph(tmp_path, {"scripts/build.ts": ["src/features/cart/a.ts"]})
        entries, counts = detect_layer_violations(tmp_path, graph, LAYERS)
        assert entries == [] and counts.eligible_edges == 0

    def test_longest_prefix_wins(self, tmp_path):
        layers = (Layer("ui", ("src/shared/ui",)), Layer("shared", ("src/shared",)))
        graph = _graph(tmp_path, {"src/shared/ui/button.ts": ["src/shared/cn.ts"]})
        assert detect_layer_violations(tmp_path, graph, layers)[0] == []
        graph = _graph(tmp_path, {"src/shared/cn.ts": ["src/shared/ui/button.ts"]})
        assert len(detect_layer_violations(tmp_path, graph, layers)[0]) == 1

    def test_no_layers_no_findings(self, tmp_path):
        graph = _graph(tmp_path, {"src/shared/a.ts": ["src/features/x/b.ts"]})
        entries, counts = detect_layer_violations(tmp_path, graph, ())
        assert entries == [] and counts.eligible_edges == 0


class TestBoundaryCandidates:
    def test_shared_file_used_by_one_slice(self, tmp_path):
        _write(tmp_path, "src/shared/cart-math.ts", 40)
        graph = _graph(
            tmp_path,
            {
                "src/features/cart/a.ts": ["src/shared/cart-math.ts"],
                "src/features/cart/b.ts": ["src/shared/cart-math.ts"],
            },
        )
        entries, total = detect_boundary_candidates(tmp_path, graph, LAYERS)
        assert total == 1
        assert len(entries) == 1
        assert entries[0]["sole_slice"] == "src/features/cart"
        assert entries[0]["importer_count"] == 2
        assert entries[0]["loc"] == 40

    def test_used_by_two_slices_or_outside_is_not_a_candidate(self, tmp_path):
        _write(tmp_path, "src/shared/a.ts")
        _write(tmp_path, "src/shared/b.ts")
        graph = _graph(
            tmp_path,
            {
                "src/features/cart/x.ts": ["src/shared/a.ts", "src/shared/b.ts"],
                "src/features/auth/y.ts": ["src/shared/a.ts"],
                "src/app/main.ts": ["src/shared/b.ts"],
            },
        )
        entries, _ = detect_boundary_candidates(tmp_path, graph, LAYERS)
        assert entries == []

    def test_skips_barrels_and_skip_dirs(self, tmp_path):
        _write(tmp_path, "src/shared/index.ts")
        _write(tmp_path, "src/shared/ui/button.tsx")
        graph = _graph(
            tmp_path,
            {"src/features/cart/x.ts": ["src/shared/index.ts", "src/shared/ui/button.tsx"]},
        )
        entries, _ = detect_boundary_candidates(
            tmp_path,
            graph,
            LAYERS,
            skip_basenames={"index.ts"},
            skip_dirs=("src/shared/ui",),
        )
        assert entries == []

    def test_sliced_layer_files_are_not_candidates(self, tmp_path):
        _write(tmp_path, "src/entities/user/model.ts")
        graph = _graph(tmp_path, {"src/features/cart/x.ts": ["src/entities/user/model.ts"]})
        entries, total = detect_boundary_candidates(tmp_path, graph, LAYERS)
        assert entries == [] and total == 0
