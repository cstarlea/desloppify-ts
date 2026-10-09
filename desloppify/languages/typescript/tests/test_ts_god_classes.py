"""God-class rules: class metrics from the syntax tree and the structural signal."""

from pathlib import Path

import pytest

from desloppify.engine.detectors.gods import detect_gods
from desloppify.languages.typescript import phases_structural as phases_structural_mod
from desloppify.languages.typescript.extractors_classes import extract_ts_classes
from desloppify.languages.typescript.phases_config import TS_CLASS_GOD_RULES
from desloppify.languages.typescript.syntax.tree import get_parser

pytestmark = pytest.mark.skipif(
    get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar"
)


@pytest.fixture(autouse=True)
def _root(set_project_root):
    """Point PROJECT_ROOT at the tmp directory."""


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return name


def _metrics(tmp_path: Path, name: str, content: str) -> dict[str, dict]:
    filepath = _write(tmp_path, name, content)
    return {c.name: {**c.metrics, "loc": c.loc} for c in extract_ts_classes(tmp_path, [filepath])}


def _class(name: str, *, methods: int = 0, deps: int = 0, filler: int = 0, route_decorators: int = 0) -> str:
    params = ", ".join(f"private readonly d{i}: D{i}" for i in range(deps))
    body = [f"  constructor({params}) {{}}"]
    for i in range(methods):
        body.extend(f"  @Dec{j}()" for j in range(route_decorators))
        body.append(f"  m{i}() {{")
        body.extend(f"    this.d{i}; // {k}" for k in range(filler))
        body.append("  }")
    return f"export class {name} {{\n" + "\n".join(body) + "\n}\n"


def test_metrics_count_methods_deps_and_decorators(tmp_path):
    metrics = _metrics(
        tmp_path,
        "src/svc.ts",
        """
@Injectable()
export class UsersController {
  @Input() name: string;
  @Column() @IsString() email: string;
  private http = inject(HttpClient);
  onClick = () => {};
  constructor(@Inject(TOKEN) private readonly a: A, b: B) {}
  @Get(':id') find(@Param('id') id: string) {}
  get total() { return 1 }
  static create() {}
  overload(): void;
}
""",
    )
    assert metrics["UsersController"] == {
        "methods": 3,  # find, create and the arrow field; accessors and overloads don't count
        "constructor_deps": 3,  # a, b and inject(HttpClient)
        "decorators": 4,  # @Injectable, @Inject, @Get, @Param; field decorators don't count
        "loc": 11,
    }


def test_metrics_skip_anonymous_classes_and_declaration_files(tmp_path):
    assert _metrics(tmp_path, "src/a.ts", "export default class { m() {} }\nconst X = class { m() {} };\n") == {
        "X": {"methods": 1, "constructor_deps": 0, "decorators": 0, "loc": 1}
    }
    filepath = _write(tmp_path, "src/b.d.ts", "export declare class Y { m(): void; }\n")
    assert extract_ts_classes(tmp_path, [filepath]) == []


def _gods(tmp_path: Path, source: str) -> list[str]:
    filepath = _write(tmp_path, "src/x.ts", source)
    entries, _ = detect_gods(extract_ts_classes(tmp_path, [filepath]), TS_CLASS_GOD_RULES, min_reasons=2)
    return [e["name"] for e in entries]


def test_two_rules_flag_a_class(tmp_path):
    # 20 methods over 323 lines
    assert _gods(tmp_path, _class("Big", methods=20, filler=14)) == ["Big"]


def test_one_rule_alone_does_not_flag(tmp_path):
    assert _gods(tmp_path, _class("Fluent", methods=40)) == []
    # A NestJS-style service with six injected collaborators
    assert _gods(tmp_path, _class("Service", deps=6, methods=6, filler=50)) == []
    # A decorated controller: every route stacks decorators
    assert _gods(tmp_path, _class("Controller", methods=12, route_decorators=4)) == []


def test_injected_deps_with_size_flag(tmp_path):
    assert _gods(tmp_path, _class("Facade", deps=7, methods=6, filler=50)) == ["Facade"]


class _Lang:
    complexity_map: dict = {}
    large_threshold = 10_000
    complexity_threshold = 10_000

    def __init__(self, files):
        self._files = files

    def file_finder(self, _path):
        return self._files


def test_structural_signal_names_god_classes_per_file(tmp_path):
    a = _write(tmp_path, "src/a.ts", _class("Big", methods=20, filler=14) + _class("Bigger", methods=21, filler=14))
    b = _write(tmp_path, "src/b.ts", _class("Small", methods=3))
    issues, _ = phases_structural_mod._detect_structural_signals(tmp_path, _Lang([a, b]))
    assert [issue["id"] for issue in issues] == ["structural::src/a.ts"]
    issue = issues[0]
    assert issue["summary"] == "Large file: god classes Big, Bigger"
    assert [c["name"] for c in issue["detail"]["god_classes"]] == ["Big", "Bigger"]
    assert issue["detail"]["god_classes"][0]["reasons"] == ["20 methods", "323 LOC"]
