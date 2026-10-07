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
  - `make lint`
- `typecheck`:
  - `make typecheck`
- `arch-contracts`:
  - `make arch`
- `ci-contracts`:
  - `make ci-contracts` (workflow/docs/policy contract tests)
- `tests-core`:
  - `make tests PYTEST_XML=pytest-core.xml`
- `tests-full`:
  - `make tests-full PYTEST_XML=pytest-full.xml`
- `tests-golden-node`:
  - `make tests-golden-node` (TypeScript golden scans with pinned tsc/knip from
    `desloppify/languages/typescript/tests/golden/node`)
- `package-smoke`:
  - `make package-smoke`

Artifacts uploaded:
- `pytest-core-report`
- `pytest-full-report`
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
- `CI / tests-core`
- `CI / tests-full`
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

Use the `Makefile` targets:

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
