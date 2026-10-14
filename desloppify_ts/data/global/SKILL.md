---
name: desloppify-ts
description: >
  Desloppify TS, a TypeScript and JavaScript codebase health scanner. Use when
  the user explicitly asks to run desloppify-ts, scan for technical debt, get a
  health score, or create a cleanup plan. Do NOT trigger for general code
  review, renaming, or fixing individual bugs.
---

<!-- desloppify-ts-begin -->
<!-- desloppify-ts-skill-version: 12 -->

# Desloppify TS

## 1. Your Job

Maximise the **strict score** honestly. Your main cycle: **scan → plan → execute → rescan**. Follow the scan output's **INSTRUCTIONS FOR AGENTS** — don't substitute your own analysis.

**Don't be lazy.** Do large refactors and small detailed fixes with equal energy. If it takes touching 20 files, touch 20 files. If it's a one-line change, make it. No task is too big or too small — fix things properly, not minimally.

## 2. The Workflow

Three phases, repeated as a cycle.

### Monorepos and multi-project directories

If the workspace contains multiple programs (e.g., frontend + backend in sibling folders), scan each one separately — do not scan the parent directory:

```bash
desloppify-ts scan --path ./web
desloppify-ts scan --path ./admin
```

Each `--path` target should be a single coherent project. Scanning a parent that contains multiple programs mixes state and path context, producing unreliable results.

### Phase 1: Scan and review — understand the codebase

```bash
desloppify-ts scan --path .       # analyse the codebase
desloppify-ts status              # check scores — are we at target?
```

After scanning, **always run `desloppify-ts next`** — it tells you exactly what to do, in order. Don't interpret the scan output yourself or ask the user what to do. Just run `next` and follow its instructions.

Before the first subjective review, `next` serves the objective work first; the review is optional and doesn't block `scan`. When the objective queue is done, `next` points at it. To trigger a review manually:
```bash
desloppify-ts review --prepare    # then follow your runner's review workflow
```

### Phase 2: Plan — decide what to work on

After reviews, triage stages and plan creation appear in the execution queue surfaced by `next`. Complete them in order — `next` tells you what each stage expects in the `--report`:
```bash
desloppify-ts next                                        # shows the next execution workflow step
desloppify-ts plan triage --stage observe --report "themes and root causes..."
desloppify-ts plan triage --stage reflect --report "comparison against completed work..."
desloppify-ts plan triage --stage organize --report "summary of priorities..."
desloppify-ts plan triage --complete --strategy "execution plan..."
```

For automated triage: `desloppify-ts plan triage --run-stages --runner codex` (Codex), `--runner claude` (Claude), or `--runner rovodev` (Rovo Dev). Options: `--only-stages`, `--dry-run`, `--stage-timeout-seconds`.

Then shape the queue. **The plan shapes everything `next` gives you** — `next` is the execution queue, not the full backlog. Don't skip this step.

```bash
desloppify-ts plan                          # see the living plan details
desloppify-ts plan queue                    # compact execution queue view
desloppify-ts plan reorder <pat> top        # reorder — what unblocks the most?
desloppify-ts plan cluster create <name>    # group related issues to batch-fix
desloppify-ts plan focus <cluster>          # scope next to one cluster
desloppify-ts plan skip <pat>              # defer — hide from next
```

### Phase 3: Execute — grind the queue to completion

Trust the plan and execute. Rescan after a batch of fixes to verify them: a plain `desloppify-ts scan` runs while the queue holds only detector findings, and the plan-start score stays frozen until the queue is clear.

**Branch first.** Create a dedicated branch — never commit health work directly to main:
```bash
git checkout -b desloppify_ts/code-health    # or desloppify_ts/<focus-area>
desloppify-ts config set commit_pr 42        # link a PR for auto-updated descriptions
```

**The loop:**
```bash
# 1. Get the next item from the execution queue
desloppify-ts next

# 2. Fix the issue in code

# 3. Resolve it: next prints the exact command. --note needs 50+ characters;
#    --confirm turns it into the attestation.
desloppify-ts plan resolve "<id>" --note "<what you did>" --confirm

# 4. When you have a logical batch, commit and record
git add <files> && git commit -m "desloppify-ts: fix 3 deferred_import findings"
desloppify-ts plan commit-log record      # moves findings uncommitted → committed, updates PR

# 5. Push periodically
git push -u origin desloppify_ts/code-health

# 6. Repeat until the queue is empty
```

Score may temporarily drop after fixes — cascade effects are normal, keep going.
If `next` suggests an auto-fixer, run `desloppify-ts autofix <fixer> --dry-run` to preview, then apply. If a fixer says it is marked unsafe, fix those issues by hand rather than passing `--unsafe`. Applying a fixer resolves its issues in the plan as well, so there is nothing to `plan resolve` afterwards; when `next` shows "Ready to scan", run `desloppify-ts scan`.

**When the queue is clear, go back to Phase 1.** New issues will surface, cascades will have resolved, priorities will have shifted. This is the cycle.

## 3. Reference

### Key concepts

- **Tiers**: T1 auto-fix → T2 quick manual → T3 judgment call → T4 major refactor.
- **Auto-clusters**: related findings are auto-grouped in `next`. Drill in with `next --cluster <name>`.
- **Zones**: production/script (scored), test/config/generated/vendor (not scored). Fix with `zone set <file|dir|'glob'> <zone>`; a directory or glob also covers files added later, and an exact file beats a pattern.
- **Wontfix cost**: widens the lenient↔strict gap. Challenge past decisions when the gap grows.

### Scoring

Overall score = **25% mechanical** + **75% subjective**.

- **Mechanical (25%)**: auto-detected issues — duplication, dead code, smells, unused imports, security. Fixed by changing code and rescanning.
- **Subjective (75%)**: design quality review — naming, error handling, abstractions, clarity. Starts at **0%** until reviewed. The scan will prompt you when a review is needed.
- **Strict score** is the north star: wontfix items count as open. The gap between overall and strict is your wontfix debt.
- **Score types**: overall (lenient), strict (wontfix counts), objective (mechanical only), verified (manual fixes count once a rescan confirms them). Fixing the code and rescanning clears an issue in every score.

### Reviews

Four paths to get subjective scores:

- **Local runner (Codex)**: `desloppify-ts review --run-batches --runner codex --parallel --scan-after-import` — automated end-to-end.
- **Local runner (Claude)**: `desloppify-ts review --prepare` → launch parallel subagents → `desloppify-ts review --import merged.json` — see skill doc overlay for details.
- **Local runner (Rovo Dev)**: `desloppify-ts review --run-batches --runner rovodev --parallel --scan-after-import` — automated end-to-end via `acli rovodev run` subprocesses.
- **Cloud/external**: `desloppify-ts review --external-start --external-runner claude` → follow session template → `--external-submit`.
- **Manual path**: `desloppify-ts review --prepare` → review per dimension → `desloppify-ts review --import file.json`.

**Batch output vs import filenames:** Individual batch outputs from subagents must be named `batch-N.raw.txt` (plain text/JSON content, `.raw.txt` extension). The `.json` filenames in `--import merged.json` or `--import findings.json` refer to the final merged import file, not individual batch outputs. Do not name batch outputs with a `.json` extension.

**Subagent parallelism limit:** Do not launch every review batch at once. Run subagents in small waves, usually **3-5 concurrent agents**, and wait for a wave to finish before starting the next. If agents return empty, partial, or rate-limit-shaped results, reduce the wave size and retry only failed batches. Launching 20+ subagents at once can exhaust API quota and produce no usable review output.

- Import first, fix after — import creates tracked state entries for correlation.
- Target-matching scores trigger auto-reset to prevent gaming. Use the blind-review workflow described in your agent overlay doc (e.g. `docs/CLAUDE.md`, `docs/HERMES.md`).
- Even moderate scores (60-80) dramatically improve overall health.
- Stale dimensions auto-surface in `next` — just follow the queue.

**Integrity rules:** Score from evidence only — no prior chat context, score history, or target-threshold anchoring. When evidence is mixed, score lower and explain uncertainty. Assess every requested dimension; never drop one.

#### Review output format

Return machine-readable JSON for review imports. For `--external-submit`, include `session` from the generated template:

```json
{
  "session": {
    "id": "<session_id_from_template>",
    "token": "<session_hmac_from_template>"
  },
  "assessments": {
    "<dimension_from_query>": 0
  },
  "findings": [
    {
      "dimension": "<dimension_from_query>",
      "identifier": "short_id",
      "summary": "one-line defect summary",
      "related_files": ["relative/path/to/file.ts"],
      "evidence": ["specific code observation"],
      "suggestion": "concrete fix recommendation",
      "confidence": "high|medium|low"
    }
  ]
}
```

`findings` MUST match `query.system_prompt` exactly (including `related_files`, `evidence`, and `suggestion`). Use `"findings": []` when no defects found. Import is fail-closed: invalid findings abort unless `--allow-partial` is passed. Assessment scores are auto-applied from trusted internal or cloud session imports. Legacy `--attested-external` remains supported.

#### Import paths

- Robust session flow (recommended): `desloppify-ts review --external-start --external-runner claude` → use generated prompt/template → run printed `--external-submit` command.
- Durable scored import (legacy): `desloppify-ts review --import findings.json --attested-external --attest "I validated this review was completed without awareness of overall score and is unbiased."`
- Findings-only fallback: `desloppify-ts review --import findings.json`

#### Reviewer agent prompt

Runners that support agent definitions (Cursor, Copilot, Gemini) can create a dedicated reviewer agent. Use this system prompt:

```
You are a code quality reviewer. You will be given a codebase path, a set of
dimensions to score, and what each dimension means. Read the code, score each
dimension 0-100 from evidence only, and return JSON in the required format.
Do not anchor to target thresholds. When evidence is mixed, score lower and
explain uncertainty.
```

See your editor's overlay section below for the agent config format.

### Plan commands

```bash
desloppify-ts plan reorder <cluster> top       # move all cluster members at once
desloppify-ts plan reorder <a> <b> top        # mix clusters + findings in one reorder
desloppify-ts plan reorder <pat> before -t X  # position relative to another item/cluster
desloppify-ts plan cluster reorder a,b top    # reorder multiple clusters as one block
desloppify-ts plan resolve <pat> --note "<what you did>" --confirm  # mark complete
desloppify-ts plan reopen <pat>               # reopen
desloppify-ts backlog                          # broader non-execution backlog
```

### Commit tracking

```bash
desloppify-ts plan commit-log                      # see uncommitted + committed status
desloppify-ts plan commit-log record               # record HEAD commit, update PR description
desloppify-ts plan commit-log record --note "why"  # with rationale
desloppify-ts plan commit-log record --only "smells::*"  # record specific findings only
desloppify-ts plan commit-log history              # show commit records
desloppify-ts plan commit-log pr                   # preview PR body markdown
desloppify-ts config set commit_tracking_enabled false  # disable guidance
```

After resolving findings as `fixed`, by `plan resolve` or by `autofix`, the tool shows uncommitted work (saying how much of it autofix did), committed history, and a suggested commit message. After committing externally, run `record` to move findings from uncommitted to committed and auto-update the linked PR description.

### Agent directives

Directives are messages shown to agents at lifecycle phase transitions — use them to switch models, set constraints, or give context-specific instructions.

```bash
desloppify-ts directives                     # show all configured directives
desloppify-ts directives set execute "Switch to claude-sonnet-4-6. Focus on speed."
desloppify-ts directives set triage "Switch to claude-opus-4-6. Read carefully."
desloppify-ts directives set review "Use blind packet. Do not anchor on previous scores."
desloppify-ts directives unset execute       # remove a directive
```

Available phases: `execute`, `review`, `triage`, `workflow`, `scan` (and fine-grained variants like `review_initial`, `triage_postflight`, etc.).

### Quick reference

```bash
desloppify-ts next --count 5                         # top 5 execution items
desloppify-ts next --cluster <name>                  # drill into a cluster
desloppify-ts backlog --count 5                      # top 5 backlog items outside execution
desloppify-ts show <pattern>                         # filter by file/detector/ID
desloppify-ts show --status open                     # all open findings
desloppify-ts plan skip --permanent "<id>" --note "reason" \
  --attest "I have reviewed this against the code and I am not gaming the score by skipping a real defect."  # accept debt
desloppify-ts exclude <path>                         # exclude a directory from scanning
desloppify-ts config show                            # show all config including excludes
desloppify-ts scan --path . --reset-subjective       # reset subjective baseline to 0
```

## 4. Fix Tool Issues Upstream

When desloppify-ts itself appears wrong or inconsistent — a bug, a bad detection, a crash, confusing output — **fix it and open a PR**. If you can't confidently fix it, file an issue instead.

### Fix and PR (preferred)

Clone the tool repo to a temp directory, make the fix there, and verify it works against the project you're scanning before pushing.

```bash
git clone https://github.com/cstarlea/desloppify-ts.git /tmp/desloppify-ts-fix
cd /tmp/desloppify-ts-fix
git checkout -b fix/<short-description>
```

Make your changes, then run the test suite and verify the fix against the original project:

```bash
python -m pytest desloppify_ts/tests/ -q
python -m desloppify_ts scan --path <project-root>   # the project you were scanning
```

Once it looks good, push and open a PR:

```bash
git add <files> && git commit -m "fix: <what and why>"
git push -u origin fix/<short-description>
gh pr create --title "fix: <short description>" --body "$(cat <<'EOF'
## Problem
<what went wrong — include the command and output>

## Fix
<what you changed and why>
EOF
)"
```

Clean up after: `rm -rf /tmp/desloppify-ts-fix`

### File an issue (fallback)

If the fix is unclear or the change needs discussion, open an issue at `https://github.com/cstarlea/desloppify-ts/issues` with a minimal repro: command, path, expected output, actual output.

## Prerequisite

`command -v desloppify-ts >/dev/null 2>&1 && echo "desloppify-ts: installed" || echo "NOT INSTALLED — run: uv tool install 'desloppify-ts[full]' && desloppify-ts setup"`

If `uv` is not available: `pip install 'desloppify-ts[full]' && desloppify-ts setup`

<!-- desloppify-ts-end -->
