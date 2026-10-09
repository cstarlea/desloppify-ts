# CI/CD Plan

This document defines the repository CI/CD operating model and required checks.

## Goals

1. Block merges unless quality gates pass.
2. Decouple package publishing from ordinary pushes.
3. Keep expensive integration checks visible and reproducible.

## Workflows

### 1) CI (`.github/workflows/ci.yml`)

Triggers:
- `pull_request`
- `push` to `main`

Required jobs:
- `lint`:
  - `make lint` (the full configured `ruff check` plus `ruff format --check`)
- `typecheck`:
  - `make typecheck`
- `arch-contracts`:
  - `make arch`
- `ci-contracts`:
  - `make ci-contracts` (workflow/docs/policy contract tests)
- `tests-core` (Python 3.11, 3.12, 3.13 and 3.14):
  - `make tests PYTEST_XML=pytest-core.xml`
- `tests-windows` (Python 3.11 on `windows-latest`, bash shell):
  - `make tests PYTEST_XML=pytest-windows.xml`
- `tests-full` (Python 3.11 and 3.14):
  - `make tests-full PYTEST_XML=pytest-full.xml`
- `tests-golden-node`:
  - `make tests-golden-node` (TypeScript golden scans with pinned tsc/knip from
    `desloppify/languages/typescript/tests/golden/node`)
- `package-smoke`:
  - `make package-smoke`

Every pytest run has a per-test `--timeout` (`PYTEST_TIMEOUT`, default 120s,
from pytest-timeout).

Artifacts uploaded:
- `pytest-core-report-<python>`
- `pytest-windows-report`
- `pytest-full-report-<python>`
- `dist-packages`

### 2) Publish (`.github/workflows/python-publish.yml`)

Triggers:
- `release.published`
- `push` tag `v*`
- `workflow_dispatch`

Safety gates before publish:
- Validate tag version matches `pyproject.toml` version (for tag pushes)
- Skip publish if version already exists on PyPI
- Run `make package-smoke`

## Branch Protection Policy (`main`)

Required status checks:
- `CI / lint`
- `CI / typecheck`
- `CI / arch-contracts`
- `CI / ci-contracts`
- `CI / tests-core (3.11)`
- `CI / tests-core (3.12)`
- `CI / tests-core (3.13)`
- `CI / tests-core (3.14)`
- `CI / tests-windows`
- `CI / tests-full (3.11)`
- `CI / tests-full (3.14)`
- `CI / tests-golden-node`
- `CI / package-smoke`

Pull request policy:
- Require PRs before merging
- Require at least 1 approving review
- Dismiss stale approvals on new commits
- Require conversation resolution

Enforcement notes:
- Admin enforcement can be enabled later after workflow stability is proven.

## Local Parity Commands

Install once per venv, then use the `Makefile` targets. The gate targets
never `pip install`; CI runs the install target as its own step.

- `make install-dev`: the package plus the pinned tools from the `dev` extra
- `make install-full`: the same plus the `full` extra (tree-sitter, Pillow, PyYAML)
- `make ci-fast`: lint + typecheck + import contracts + tests
- `make ci`: `ci-fast` + full tests + package smoke
- `make ci-contracts`: verify CI/workflow/docs contracts

## Rollout

Phase 1 (immediate):
- Add workflows + local parity targets
- Enable branch protection with required CI checks

Phase 2 (stabilization):
- Expand mypy coverage gradually by directory

Phase 3 (hardening):
- Enable admin enforcement for branch protection if desired
