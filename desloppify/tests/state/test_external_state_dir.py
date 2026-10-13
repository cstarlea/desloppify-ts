"""An explicit --state outside the project keeps every write out of the project."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.cli as cli_mod
from desloppify.app.output.scorecard import get_badge_config
from desloppify.base.config import load_config, save_config
from desloppify.base.discovery.paths import get_state_dir
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.base.search.query_paths import query_file_path
from desloppify.engine._plan.persistence import get_plan_file

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.ts").write_text("export const a = 1;\n")
    return repo


def _run_main(monkeypatch, argv: list[str], cwd: Path) -> list[Path]:
    seen: list[Path] = []
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.argv", ["desloppify", *argv])
    monkeypatch.setattr(
        cli_mod,
        "_resolve_handler",
        lambda _cmd: lambda _args: seen.append(get_state_dir()),
    )
    cli_mod.main()
    return seen


@pytest.mark.parametrize("argv", [["zone", "show"], ["scan"]])
def test_state_elsewhere_takes_locks_next_to_state(tmp_path, monkeypatch, argv):
    monkeypatch.delenv("DESLOPPIFY_ROOT", raising=False)
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "out"
    state = elsewhere / "state.json"
    command, *rest = argv
    seen = _run_main(
        monkeypatch,
        [command, "--path", str(repo), "--state", str(state), *rest],
        tmp_path,
    )
    assert seen == [elsewhere.resolve()]
    assert not (repo / ".desloppify").exists()
    assert (elsewhere / "plan.lock").exists()


def test_state_inside_project_keeps_project_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DESLOPPIFY_ROOT", raising=False)
    repo = _repo(tmp_path)
    state = repo / ".desloppify" / "state.json"
    seen = _run_main(
        monkeypatch, ["scan", "--path", str(repo), "--state", str(state)], tmp_path
    )
    assert seen == [repo.resolve() / ".desloppify"]


def test_external_state_dir_moves_plan_query_and_config(tmp_path):
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "out"
    with runtime_scope(RuntimeContext(project_root=repo, state_dir=elsewhere)):
        assert get_plan_file() == elsewhere.resolve() / "plan.json"
        assert query_file_path() == elsewhere.resolve() / "query.json"


def test_external_state_reads_project_config_but_saves_elsewhere(tmp_path):
    repo = _repo(tmp_path)
    project_config = repo / ".desloppify" / "config.json"
    project_config.parent.mkdir()
    project_config.write_text(json.dumps({"exclude": ["vendor"]}))
    before = project_config.read_text()
    elsewhere = tmp_path / "out"
    with runtime_scope(RuntimeContext(project_root=repo, state_dir=elsewhere)):
        config = load_config()
        assert config["exclude"] == ["vendor"]
        config["exclude"].append("gen")
        save_config(config)
        assert load_config()["exclude"] == ["vendor", "gen"]
    assert project_config.read_text() == before
    assert sorted(p.name for p in project_config.parent.iterdir()) == ["config.json"]
    assert (elsewhere / "config.json").exists()


def test_default_scorecard_goes_next_to_external_state(tmp_path, monkeypatch):
    monkeypatch.delenv("DESLOPPIFY_BADGE_PATH", raising=False)
    monkeypatch.delenv("DESLOPPIFY_NO_BADGE", raising=False)
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "out"
    with runtime_scope(RuntimeContext(project_root=repo, state_dir=elsewhere)):
        path, disabled = get_badge_config(SimpleNamespace(), {})
    assert not disabled
    assert path == elsewhere.resolve() / "scorecard.png"


@needs_git
class TestTrackedScorecard:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DESLOPPIFY_BADGE_PATH", raising=False)
        monkeypatch.delenv("DESLOPPIFY_NO_BADGE", raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "scorecard.png").write_bytes(b"PNG")
        _git(repo, "add", "scorecard.png")
        with runtime_scope(RuntimeContext(project_root=repo)):
            yield repo

    def test_default_does_not_overwrite_tracked_file(self, repo):
        path, _ = get_badge_config(SimpleNamespace(), {"badge_path": "scorecard.png"})
        assert path == repo.resolve() / ".desloppify" / "scorecard.png"

    def test_untracked_default_stays_in_root(self, repo):
        _git(repo, "rm", "-q", "--cached", "scorecard.png")
        path, _ = get_badge_config(SimpleNamespace(), {})
        assert path == repo.resolve() / "scorecard.png"

    def test_cli_badge_path_opts_in(self, repo):
        path, _ = get_badge_config(SimpleNamespace(badge_path="scorecard.png"), {})
        assert path == repo.resolve() / "scorecard.png"

    def test_env_badge_path_opts_in(self, repo, monkeypatch):
        monkeypatch.setenv("DESLOPPIFY_BADGE_PATH", "scorecard.png")
        path, _ = get_badge_config(SimpleNamespace(), {})
        assert path == repo.resolve() / "scorecard.png"

    def test_configured_badge_path_opts_in(self, repo):
        (repo / "assets").mkdir()
        (repo / "assets" / "card.png").write_bytes(b"PNG")
        _git(repo, "add", "assets/card.png")
        path, _ = get_badge_config(SimpleNamespace(), {"badge_path": "assets/card.png"})
        assert path == repo.resolve() / "assets" / "card.png"
