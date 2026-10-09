# desloppify-ts - an agent harness to make your TypeScript codebase 🤌

[![PyPI version](https://img.shields.io/pypi/v/desloppify-ts)](https://pypi.org/project/desloppify-ts/) [![CI](https://github.com/cstarlea/desloppify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cstarlea/desloppify/actions/workflows/ci.yml) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

desloppify-ts gives your AI coding agent the tools to identify, understand, and systematically improve the quality of a TypeScript or JavaScript codebase. It combines mechanical detection (dead code, duplication, complexity, unused exports, import cycles) with subjective LLM review (naming, abstractions, module boundaries), then works through a prioritized fix loop. State persists across scans so it chips away over multiple sessions, and the scoring is designed to resist gaming.

It scans `.ts`/`.tsx`/`.mts`/`.cts` and `.js`/`.jsx`/`.mjs`/`.cjs` files together, whether the project is plain JavaScript, mid-migration (`allowJs`) or TypeScript with a few JS files. When Node is available it runs the project's own `tsc` and `knip` for unused-code detection.

desloppify-ts is a TypeScript-only fork of [desloppify](https://github.com/peteromallet/desloppify) by Peter O'Malley, which is no longer maintained. The other languages were removed to keep the tool focused; the last multi-language version is tagged `pre-ts-only`. See [NOTICE](https://github.com/cstarlea/desloppify/blob/main/NOTICE) for credits.

## Install

Requires Python 3.11+. The PyPI package is `desloppify-ts`; the command it installs is still `desloppify`.

```bash
uvx desloppify-ts scan --path .           # try it without installing
uv tool install "desloppify-ts[full]"     # or install it as a tool
pip install --upgrade "desloppify-ts[full]"
```

The `[full]` extra adds tree-sitter (accurate import parsing and the syntax-tree fixers), scorecard images and YAML plan export. A `desloppify-ts` command is installed as an alias of `desloppify`, which is what makes the bare `uvx desloppify-ts` work. The language pack fetches its grammars on first use; run `desloppify setup --grammars` once with network access (for example before going offline or in a CI image) to download and check them.

Coming from upstream `desloppify`? Uninstall it first (`pip uninstall desloppify`), because both packages install the same `desloppify` module and command. Existing `.desloppify/` state, config and skill files keep working.

<img src="https://raw.githubusercontent.com/cstarlea/desloppify/main/assets/explained.png" width="100%">

The score gives your agent a north-star, and the tooling helps it plan, execute, and resolve issues until it hits your target — with a lot of tricks to keep it on track. A score above 98 should correlate with a codebase a seasoned engineer would call beautiful.

That score generates a scorecard badge for your GitHub profile or README:

<img src="https://raw.githubusercontent.com/cstarlea/desloppify/main/assets/scorecard.png" width="100%">

## For your agent's consideration...

Paste this prompt into your agent:

```
I want you to improve the quality of this codebase. To do this, install and run desloppify
(PyPI package desloppify-ts; the command is desloppify).
Run ALL of the following (requires Python 3.11+):

pip install --upgrade "desloppify-ts[full]"
desloppify update-skill claude    # installs the full workflow guide — pick yours: claude, cursor, codex, copilot, droid, windsurf, gemini, rovodev

Add .desloppify/ to your .gitignore — it contains local state that shouldn't be committed.

Before scanning, check for directories that should be excluded (vendor, build output,
generated code, worktrees, etc.) and exclude obvious ones with `desloppify exclude <path>`.
Share any questionable candidates with me before excluding.

desloppify scan --path .
desloppify next

--path is the directory to scan (use "." for the whole project, or "src/" etc).

Your goal is to get the strict score as high as possible. The scoring resists gaming — the
only way to improve it is to actually make the code better.

THE LOOP: run `next`. It is the execution queue from the living plan, not the whole backlog.
It tells you what to fix now, which file, and the resolve command to run when done.
Fix it, resolve it, run `next` again. Over and over. This is your main job.

Use `desloppify backlog` only when you need to inspect broader open work that is not currently
driving execution.

Don't be lazy. Large refactors and small detailed fixes — do both with equal energy. No task
is too big or too small. Fix things properly, not minimally.

Use `plan` / `plan queue` to reorder priorities or cluster related issues. Rescan periodically.
The scan output includes agent instructions — follow them, don't substitute your own analysis.
```

## Monorepos and multi-project directories

Each `--path` target should be a single coherent project. A pnpm, npm or yarn workspace counts as one project: scan it from the workspace root so imports between packages resolve.

```bash
desloppify scan --path .
```

## CI

Desloppify works best in CI as a full-codebase health gate, not as a diff-only linter. Run the CI profile against the same coherent project path you scan locally:

```bash
desloppify scan --path . --profile ci --no-badge --fail-under 80
```

`--profile ci` skips slow and subjective phases and bypasses the mid-cycle scan queue gate so a CI job can collect a fresh mechanical snapshot. It prints a plain report (scores, mechanical dimensions, issue counts, coverage warnings) with no colour, agent instructions or workflow nudges. `--fail-under SCORE` exits with status 1 when the objective score is below `SCORE`; `--fail-score verified|strict|overall` checks another score instead. Without a subjective review, overall and strict count every subjective dimension as 0, so in CI the objective or verified score is usually the one to gate on. `status --json` still gives a script the full score breakdown.

Minimal GitHub Actions example:

```yaml
name: desloppify

on:
  pull_request:
  push:
    branches: [main]

jobs:
  health:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install --upgrade "desloppify-ts[full]"
      - run: desloppify scan --path . --profile ci --no-badge --fail-under 80
```

For monorepos, run one job or matrix entry per project path instead of scanning the workspace root. True incremental or diff-only scanning is not the supported model yet; compare full-codebase results across runs or enforce a project-level threshold.

## How it works

```
scan ──→ score ──→ review ──→ triage ──→ execute ──→ rescan
  │         │         │          │          │           │
  │     dimensions    │     prioritize    fix it     verify
  │     scored      LLM reviews  & cluster  & resolve  improvements
  │                 subjective   the queue
  │                 quality
  detectors find
  mechanical issues
  (dead code, smells,
  test gaps, etc.)
```

**Scan** runs mechanical detectors across your codebase — dead code, duplication, complexity, test coverage gaps, naming issues, and more. Each issue is scored by dimension (File health, Code quality, Test health, etc.).

**Review** uses an LLM to assess subjective quality dimensions — naming, abstractions, error handling patterns, module boundaries. These score alongside the mechanical dimensions.

**Triage** is where prioritization happens. The agent (or you) observes the findings, reflects on patterns, organizes issues into clusters, and enriches them with implementation detail. This produces an ordered execution queue — only items explicitly queued appear in `next`. Before triage, all mechanical issues are visible in the queue sorted by impact, which can be noisy.

**Execute** is the fix loop: `next` → fix → `resolve` → `next`. Items come from the triaged queue. Autofix handles what it can; the rest needs manual or agent work.

**Rescan** verifies improvements, catches cascading effects, and feeds the next cycle.

State persists in `.desloppify/` so progress carries across sessions. The scoring resists gaming — wontfix items widen the gap between lenient and strict scores, and re-reviewing dimensions can lower scores if the reviewer finds new issues.

## From Vibe Coding to Vibe Engineering

Vibe coding gets things built fast. But the codebases it produces tend to rot in ways that are hard to see and harder to fix — not just the mechanical stuff like dead imports, but the structural kind. Abstractions that made sense at first stop making sense. Naming drifts. Error handling is done three different ways. The codebase works, but working in it gets worse over time.

LLMs are actually good at spotting this now, if you ask them the right questions. That's the core bet here — that an agent with the right framework can hold a codebase to a real standard, the kind that used to require a senior engineer paying close attention over months.

So we're trying to define what "good" looks like as a score that's actually worth optimizing. Not a lint score you game to 100 by suppressing warnings. Something where improving the number means the codebase genuinely got better. That's hard, and we're not done, but the anti-gaming stuff matters to us a lot — it's the difference between a useful signal and a vanity metric.

The hope is that anyone can use this to build something a seasoned engineer would look at and respect. That's the bar we're aiming for.

If you'd like to join a community of vibe engineers who want to build beautiful things, [come hang out](https://discord.gg/aZdzbZrHaY).

<img src="https://raw.githubusercontent.com/cstarlea/desloppify/main/assets/engineering.png" width="100%">

---

Issues, improvements, and PRs are hugely appreciated — [github.com/cstarlea/desloppify](https://github.com/cstarlea/desloppify).

desloppify-ts is distributed under upstream's license, the Open Source Native License 0.2. It is free for any individual — whether working independently or at a company — to use for their own work. It is also free for open source companies to use in any capacity, including commercial. Non-open source companies who wish to commercialize it should refer to the [LICENSE](https://github.com/cstarlea/desloppify/blob/main/LICENSE) for transparent pricing details.
