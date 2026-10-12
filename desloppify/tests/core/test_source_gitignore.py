"""Source discovery honours .gitignore inside a git work tree."""

import shutil
import subprocess

import pytest

import desloppify.base.discovery.source as source_mod
from desloppify.base.discovery.source import (
    SourceDiscoveryOptions,
    clear_source_file_cache_for_tests,
    find_source_files,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from desloppify.base.runtime_state import current_runtime_context

    monkeypatch.setattr(current_runtime_context(), "project_root", tmp_path)
    clear_source_file_cache_for_tests()
    yield tmp_path
    clear_source_file_cache_for_tests()


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(root, rel_path, text="export const x = 1;\n"):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _layout(root):
    _write(root, ".gitignore", "tmp/\n*.local.ts\nios/App/App/public\n")
    for rel_path in (
        "src/tracked.ts",
        "src/untracked.ts",
        "src/secret.local.ts",
        "tmp/scratch.ts",
        "ios/App/App/public/native-bridge.js",
    ):
        _write(root, rel_path)


@needs_git
def test_gitignored_files_are_skipped(project):
    _git(project, "init", "-q")
    _layout(project)
    _git(project, "add", "src/tracked.ts")

    files = find_source_files(str(project), [".ts", ".js"])

    assert files == ["src/tracked.ts", "src/untracked.ts"]


@needs_git
def test_scan_subdirectory_of_work_tree(project):
    _git(project, "init", "-q")
    _layout(project)

    assert find_source_files(str(project / "src"), [".ts"]) == [
        "src/tracked.ts",
        "src/untracked.ts",
    ]


@needs_git
def test_exclusions_still_apply_on_top(project):
    _git(project, "init", "-q")
    _layout(project)

    files = find_source_files(
        str(project), [".ts"], SourceDiscoveryOptions(exclusions=("src/untracked.ts",))
    )

    assert files == ["src/tracked.ts"]


@needs_git
def test_nested_repository_is_walked(project):
    _git(project, "init", "-q")
    _layout(project)
    _git(project / "src", "init", "-q")
    _write(project, "src/inner/deep.ts")

    files = find_source_files(str(project), [".ts"])

    # git lists the nested repository as one entry; its contents are walked.
    assert "src/inner/deep.ts" in files
    assert "tmp/scratch.ts" not in files


@needs_git
def test_ignored_scan_root_falls_back_to_walk(project):
    _git(project, "init", "-q")
    _layout(project)

    assert find_source_files(str(project / "tmp"), [".ts"]) == ["tmp/scratch.ts"]


def test_outside_git_walks_everything(project, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(project.parent))
    _layout(project)

    files = find_source_files(str(project), [".ts", ".js"])

    assert "tmp/scratch.ts" in files
    assert "src/secret.local.ts" in files


@needs_git
def test_git_unavailable_walks_everything(project, monkeypatch):
    _git(project, "init", "-q")
    _layout(project)
    monkeypatch.setattr(source_mod.shutil, "which", lambda _name: None)

    files = find_source_files(str(project), [".ts"])

    assert files == [
        "src/secret.local.ts",
        "src/tracked.ts",
        "src/untracked.ts",
        "tmp/scratch.ts",
    ]


@needs_git
def test_git_listing_is_one_subprocess_call(project, monkeypatch):
    _git(project, "init", "-q")
    _layout(project)
    calls = []
    real_run = source_mod.subprocess.run

    def _run(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(source_mod.subprocess, "run", _run)

    find_source_files(str(project), [".ts", ".js"])

    assert len(calls) == 1
