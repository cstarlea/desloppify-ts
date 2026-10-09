from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "python-publish.yml"
CI_PLAN = REPO_ROOT / "dev" / "ci_plan.md"
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _on_block(data: dict) -> dict:
    # PyYAML parses "on" as True under YAML 1.1 rules.
    return data.get("on", data.get(True, {}))


def _run_commands(job: dict) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def _step_names(job: dict) -> list[str]:
    return [step.get("name", "") for step in job.get("steps", [])]


def _optional_dependencies() -> dict[str, list[str]]:
    doc = tomllib.loads(PYPROJECT.read_text())
    optional = doc.get("project", {}).get("optional-dependencies", {})
    return {str(key): list(value) for key, value in optional.items()}


def test_ci_workflow_jobs_are_bound_to_make_targets() -> None:
    ci = _load_yaml(CI_WORKFLOW)
    jobs = ci["jobs"]
    expected = {
        "lint": "make lint",
        "typecheck": "make typecheck",
        "arch-contracts": "make arch",
        "ci-contracts": "make ci-contracts",
        "tests-core": "make tests PYTEST_XML=pytest-core.xml",
        "tests-windows": "make tests PYTEST_XML=pytest-windows.xml",
        "tests-full": "make tests-full PYTEST_XML=pytest-full.xml",
        "tests-golden-node": "make tests-golden-node",
        "package-smoke": "make package-smoke",
    }

    assert set(expected).issubset(jobs), "CI workflow missing required jobs."

    full_jobs = {"tests-full", "tests-golden-node"}
    for job_name, expected_cmd in expected.items():
        job = jobs[job_name]
        runs = _run_commands(job)
        assert any(expected_cmd in run for run in runs), (
            f"{job_name} must execute `{expected_cmd}` for local/CI parity."
        )
        install = "make install-full" if job_name in full_jobs else "make install-dev"
        assert install in runs and runs.index(install) < next(
            i for i, run in enumerate(runs) if expected_cmd in run
        ), f"{job_name} must run `{install}` before its gate."
        assert any(
            step.get("uses") == "actions/setup-python@v5" for step in job["steps"]
        ), f"{job_name} should use actions/setup-python@v5."


def _matrix_versions(job: dict) -> list[str]:
    return [
        str(v)
        for v in job.get("strategy", {}).get("matrix", {}).get("python-version", [])
    ]


def _check_names(job_name: str, job: dict) -> list[str]:
    versions = _matrix_versions(job)
    if not versions:
        return [job_name]
    return [f"{job_name} ({version})" for version in versions]


def test_ci_tests_cover_supported_python_versions() -> None:
    jobs = _load_yaml(CI_WORKFLOW)["jobs"]
    classifiers = tomllib.loads(PYPROJECT.read_text())["project"]["classifiers"]
    declared = sorted(
        c.rsplit("::", 1)[1].strip()
        for c in classifiers
        if c.startswith("Programming Language :: Python :: 3.")
    )
    assert declared, "pyproject declares no Python versions."
    core = jobs["tests-core"]
    assert sorted(_matrix_versions(core)) == declared, (
        "tests-core must run on every Python version pyproject declares."
    )
    full = _matrix_versions(jobs["tests-full"])
    assert {declared[0], declared[-1]} <= set(full), (
        "tests-full must run on the oldest and newest supported Python."
    )
    for job_name in ("tests-core", "tests-full"):
        setup = next(
            step
            for step in jobs[job_name]["steps"]
            if step.get("uses") == "actions/setup-python@v5"
        )
        assert setup["with"]["python-version"] == "${{ matrix.python-version }}"
        assert (
            jobs[job_name]["name"] == f"{job_name} (${{{{ matrix.python-version }}}})"
        )


def test_ci_has_a_windows_core_job() -> None:
    job = _load_yaml(CI_WORKFLOW)["jobs"]["tests-windows"]
    assert job["runs-on"].startswith("windows-")
    assert job.get("defaults", {}).get("run", {}).get("shell") == "bash"


def test_makefile_tests_run_with_a_timeout() -> None:
    text = MAKEFILE.read_text()
    assert re.search(r"^PYTEST := pytest --timeout=", text, flags=re.MULTILINE)
    for target in ("tests", "tests-full", "tests-golden-node", "ci-contracts"):
        body = text.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
        assert "$(PYTEST)" in body, f"{target} must run pytest with --timeout."
        assert not re.search(r"\bpytest -q", body), (
            f"{target} runs pytest without the timeout."
        )


def test_ci_workflow_has_expected_triggers() -> None:
    ci = _load_yaml(CI_WORKFLOW)
    on_block = _on_block(ci)
    assert "pull_request" in on_block
    assert on_block.get("push", {}).get("branches") == ["main"]
    assert "workflow_call" in on_block, "publish reuses CI as its gate."


def test_publish_workflow_keeps_release_safety_gates() -> None:
    wf = _load_yaml(PUBLISH_WORKFLOW)
    on_block = _on_block(wf)
    assert set(on_block) == {"release"}, "only a published release may publish."
    assert on_block["release"].get("types") == ["published"]

    jobs = wf["jobs"]
    assert jobs["ci"].get("uses") == "./.github/workflows/ci.yml"
    publish_job = jobs["publish"]
    assert publish_job.get("needs") == "ci"
    for job in (jobs["ci"], publish_job):
        assert job.get("if") == "vars.PYPI_PUBLISH == 'true'"

    names = _step_names(publish_job)
    assert "make install-dev" in _run_commands(publish_job)
    assert "Check the release tag matches the version" in names
    assert "Check if version exists on PyPI" in names
    assert "Run packaging smoke gate" in names
    assert "Publish to PyPI" in names
    assert any("make package-smoke" in run for run in _run_commands(publish_job))


def test_publish_workflow_targets_the_distribution_name() -> None:
    name = tomllib.loads(PYPROJECT.read_text())["project"]["name"]
    wf = _load_yaml(PUBLISH_WORKFLOW)
    assert wf["jobs"]["publish"]["environment"]["url"] == f"https://pypi.org/p/{name}"


def test_makefile_contains_ci_gate_targets() -> None:
    text = MAKEFILE.read_text()
    targets = set(re.findall(r"^([a-zA-Z0-9_-]+):", text, flags=re.MULTILINE))
    expected = {
        "lint",
        "typecheck",
        "arch",
        "ci-contracts",
        "tests",
        "tests-full",
        "tests-golden-node",
        "package-smoke",
        "ci-fast",
        "ci",
    }
    assert expected.issubset(targets)


def test_ci_contracts_target_includes_phase_order_invariant() -> None:
    text = MAKEFILE.read_text()
    assert (
        "$(PYTEST) -q desloppify/tests/commands/test_lifecycle_transitions.py "
        '-k "assessment_then_score_when_no_review_followup"'
    ) in text


def test_readme_optional_extras_exist_in_pyproject() -> None:
    readme = README.read_text()
    referenced = set(re.findall(r"desloppify(?:-ts)?\[([a-zA-Z0-9_-]+)\]", readme))
    optional = _optional_dependencies()
    missing = sorted(extra for extra in referenced if extra not in optional)
    assert not missing, (
        "README references optional extras that are not defined in pyproject.toml: "
        f"{missing}"
    )


def test_full_extra_includes_all_optional_dependency_groups() -> None:
    optional = _optional_dependencies()
    full = set(optional.get("full", []))
    missing_dependencies: dict[str, list[str]] = {}
    for extra, deps in optional.items():
        if extra in {"full", "dev"}:
            continue
        extra_missing = sorted(dep for dep in deps if dep not in full)
        if extra_missing:
            missing_dependencies[extra] = extra_missing

    assert not missing_dependencies, (
        "Optional extras must stay represented in [full] so README install guidance "
        f"does not drift: {missing_dependencies}"
    )


def test_ci_plan_required_checks_match_ci_workflow() -> None:
    ci = _load_yaml(CI_WORKFLOW)
    expected_contexts = [
        f"CI / {check}"
        for name in (
            "lint",
            "typecheck",
            "arch-contracts",
            "ci-contracts",
            "tests-core",
            "tests-windows",
            "tests-full",
            "tests-golden-node",
            "package-smoke",
        )
        for check in _check_names(name, ci["jobs"][name])
    ]

    doc = CI_PLAN.read_text()
    section = doc.split("Required status checks:", 1)[1].split(
        "Pull request policy:", 1
    )[0]
    documented = re.findall(r"- `([^`]+)`", section)

    assert documented == expected_contexts
    for context in expected_contexts:
        job_name = context.split("CI / ", 1)[1].split(" (", 1)[0]
        assert job_name in ci["jobs"], f"{context} has no matching CI workflow job."


GATE_TARGETS = (
    "lint",
    "typecheck",
    "arch",
    "ci-contracts",
    "tests",
    "tests-full",
    "tests-golden-node",
    "package-smoke",
)


def _make_rule(text: str, target: str) -> tuple[str, str]:
    """A target's prerequisites and recipe."""
    match = re.search(
        rf"^{re.escape(target)}:(.*)\n((?:\t.*\n?)*)", text, flags=re.MULTILINE
    )
    assert match, f"Makefile has no `{target}` target."
    return match.group(1).strip(), match.group(2)


def test_make_gates_do_not_install_anything() -> None:
    text = MAKEFILE.read_text()
    for target in GATE_TARGETS:
        prerequisites, recipe = _make_rule(text, target)
        assert "install" not in prerequisites, (
            f"`{target}` must not depend on an install target."
        )
        assert "$(PIP)" not in recipe, (
            f"`{target}` must not pip install; use make install-dev."
        )
    for target, extras in (("install-dev", ".[dev]"), ("install-full", ".[full,dev]")):
        _prerequisites, recipe = _make_rule(text, target)
        assert f'install -e "{extras}"' in recipe


def test_dev_extra_pins_the_gate_tools() -> None:
    dev = _optional_dependencies()["dev"]
    names = {re.split(r"[=<>!~ ]", dep, maxsplit=1)[0].lower() for dep in dev}
    assert {
        "pytest",
        "pytest-xdist",
        "pytest-timeout",
        "ruff",
        "mypy",
        "import-linter",
    } <= names
    unpinned = [dep for dep in dev if "==" not in dep]
    assert not unpinned, f"dev tools must be pinned: {unpinned}"
