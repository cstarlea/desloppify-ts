# Release Checklist

Releases publish the `desloppify-ts` distribution to PyPI from `cstarlea/desloppify`. The import package and the `desloppify` command keep their names.

Replace `CURRENT` with the version being released (e.g., `1.1.0`) and `NEXT` with the following version (e.g., `1.1.1`).

## How publishing works

`.github/workflows/python-publish.yml` runs only when a GitHub release is **published**. Merging to `main` and pushing tags never publish. The workflow:

1. does nothing unless the repository variable `PYPI_PUBLISH` is `true`;
2. runs the whole CI workflow (`ci.yml`) on the release tag and stops if any job fails;
3. fails unless the tag is exactly `v` + `[project].version` from `pyproject.toml`;
4. skips the upload if that version is already on PyPI;
5. runs `make install-dev` and `make package-smoke`, which builds the sdist and wheel and installs the wheel in a fresh venv, then uploads `dist/` with PyPI trusted publishing from the `pypi` environment.

One-time setup (already done if a release has gone out):

- On pypi.org, add a trusted publisher for the project `desloppify-ts` (a "pending publisher" before the first upload): owner `cstarlea`, repository `desloppify`, workflow `python-publish.yml`, environment `pypi`.
- On GitHub, keep the `pypi` environment (Settings → Environments). Optionally add yourself as a required reviewer so each upload waits for approval.
- `gh variable set PYPI_PUBLISH --repo cstarlea/desloppify --body true`

## Setup

Create a GitHub label for the release:
```bash
gh label create "release:vCURRENT" --repo cstarlea/desloppify --description "Included in vCURRENT" --color 1D76DB
```

Tag every issue and PR that lands during this cycle with `release:vCURRENT`.

---

## Pre-Release Checklist

- [ ] `version = "CURRENT"` in `pyproject.toml` on `main` (bump it in an ordinary PR)
- [ ] CI is green on `main`
- [ ] For full local validation, `make install-full` once in a fresh venv, then `make ci` (runs `tests-full`, `tests-golden-node` and `package-smoke` too; `tests-golden-node` needs Node 22 and npm)
- [ ] Local build check:
  ```bash
  rm -rf dist && uv build
  ls dist/   # desloppify_ts-CURRENT-py3-none-any.whl and desloppify_ts-CURRENT.tar.gz
  uvx twine check dist/*
  ```
- [ ] Write release notes using the template in `dev/release/RELEASE_NOTES_TEMPLATE.md`
  - Reference past examples in `dev/release/release-notes-examples/` for tone and structure
- [ ] Release notes reviewed and saved to `dev/release-notes-drafts/vCURRENT.md`

---

## Release

- [ ] Create the GitHub release from `main`. This creates the tag and starts the publish workflow:
  ```bash
  gh release create vCURRENT --repo cstarlea/desloppify --target main \
    --title "vCURRENT" --notes-file dev/release-notes-drafts/vCURRENT.md
  ```
- [ ] Watch the run, and approve the `pypi` environment if it asks:
  ```bash
  gh run watch --repo cstarlea/desloppify \
    "$(gh run list --repo cstarlea/desloppify --workflow python-publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
  ```
- [ ] Check the upload: `uvx --from "desloppify-ts==CURRENT" desloppify --version`

If the tag doesn't match the version, the workflow fails before uploading. Delete the release and the tag (`gh release delete vCURRENT --cleanup-tag`), fix `pyproject.toml`, and release again.

---

## Post-Release Cleanup

- [ ] Notify and close the issues and PRs tagged with this release:
  ```bash
  gh issue list --repo cstarlea/desloppify --label "release:vCURRENT" --state open --json number --jq '.[].number' | while read num; do
    gh issue comment "$num" --repo cstarlea/desloppify --body "Released in vCURRENT — https://github.com/cstarlea/desloppify/releases/tag/vCURRENT"
    gh issue close "$num" --repo cstarlea/desloppify
  done

  gh pr list --repo cstarlea/desloppify --label "release:vCURRENT" --state all --json number --jq '.[].number' | while read num; do
    gh pr comment "$num" --repo cstarlea/desloppify --body "Released in vCURRENT — https://github.com/cstarlea/desloppify/releases/tag/vCURRENT"
  done
  ```
- [ ] Create the next release label:
  ```bash
  gh label create "release:vNEXT" --repo cstarlea/desloppify --description "Included in vNEXT" --color 1D76DB
  ```
