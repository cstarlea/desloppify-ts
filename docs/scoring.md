# How Scoring Works

Desloppify computes health scores from 0 to 100. 100 means no counted failures. The code lives in `desloppify/engine/_scoring/`:

- `policy/core.py` holds the weights and the per-mode failure rules.
- `detection.py` counts failures per detector.
- `results/core.py` and `results/health.py` combine them into dimension scores and the pools.
- `state_integration.py` writes the scores into state.

Every `save_state` recomputes the scores (`recompute_stats`), so they always reflect the issues currently in state.

## Four scores

| Score | Dimensions | Which statuses fail |
|---|---|---|
| **overall** | mechanical and subjective | lenient |
| **objective** | mechanical only | lenient |
| **strict** | mechanical and subjective | strict |
| **verified** | mechanical only | verified |

The modes are defined under "Lenient, strict and verified" below.

### Headline score

The scan summary, `status`, the scorecard image and `query.json` (`headline`) lead with one score (`headline_score` in `state_score_snapshot.py`). Normally that is overall. While no subjective dimension has been assessed, it is the objective score, marked **provisional**, because overall and strict can't pass 25 until then (see below). The four stored scores don't change.

### Gating CI on a score

`scan --fail-under SCORE` exits with status 1 when a score is below `SCORE`. `--fail-score` picks which one: objective (the default), verified, strict or overall. With `--profile ci`, which skips the slow and subjective phases, the scan prints a plain report that ends with the gate result.

## Two pools

Overall and strict blend two pools of dimensions (`MECHANICAL_WEIGHT_FRACTION`, `SUBJECTIVE_WEIGHT_FRACTION`):

| Pool | Share | Source |
|---|---|---|
| **Mechanical** | 25% | Detectors run by `scan` |
| **Subjective** | 75% | Assessments imported by `review` |

Each pool is a weighted average of its dimension scores.

The subjective pool always contains every default subjective dimension (20 for TypeScript). A dimension that hasn't been assessed scores 0, so **before the first review, overall and strict are at most 25**, which is a quarter of the mechanical average. That's why the headline is the provisional objective score until then. Objective and verified don't use the subjective pool.

If no detector reports any checks and there are no assessments, all four scores are 100 (`_set_perfect_scores`).

## Mechanical dimensions

Each detector reports a **potential**, the number of checks it ran, along with its issues. A dimension pools the detectors in it that have a potential above 0:

    dimension_score = (checks - weighted_failures) / checks * 100

| Dimension | Weight in pool | Detectors that TypeScript scans emit |
|---|---|---|
| **File health** | 2.0 | structural |
| **Code quality** | 1.0 | unused, logs, exports, deprecated, smells, react, nextjs, next_lint, orphaned, flat_dirs, naming, single_use, coupling, cycles, facade, props, patterns, responsibility_cohesion, stale_exclude, tsconfig_health |
| **Duplication** | 1.0 | dupes, boilerplate_duplication |
| **Test health** | 1.0 | test_coverage |
| **Security** | 1.0 | security |
| **Type checks** | 1.0 | type_error |

The detector-to-dimension mapping comes from the registry (`base/registry/catalog_entries.py`), and the weights come from `MECHANICAL_DIMENSION_WEIGHTS`. The registry also maps some detectors that no TypeScript phase emits, such as the Rust detectors. They never report a potential, so they never count. Review, concerns, `signature`, `stale_wontfix` and a few others create work items but are kept out of scoring (`SCORING_EXCLUDED_DETECTORS`).

### Sample dampening

A mechanical dimension with fewer than 200 checks (`MIN_SAMPLE`) has its weight reduced in proportion. For example, 50 checks count at 25% of the dimension's weight.

### Carried-forward dimensions

If a mechanical dimension is missing from a scan, its previous score is kept and marked `carried_forward` (`_materialize_dimension_scores`). This only happens when none of its detectors reported a potential, meaning none of them ran (for example, `dupes` under `--skip-slow`). If a detector ran and found nothing left to check, the old score is dropped.

A carried score expires. `carried_forward_since_scan` records the first scan it was carried in, and after `CARRIED_FORWARD_MAX_SCANS` (3) scans without its detectors running, the dimension drops out of the score until they run again. The count is in scans, so the recomputes on saves between scans don't age it.

### Disabled detectors and dimensions

The `disabled` config key takes detector names (`smells`) and mechanical dimensions (`Test health`; case, `_` and `-` don't matter). Set it with `desloppify config set disabled <name>`, and remove one entry with `desloppify config unset disabled <name>`. A disabled detector is taken out of scoring altogether; this is different from `ignore`, which hides issues but leaves the detector's checks in the denominator:

- Its potential is dropped and new issues from it are discarded, so its dimension is recomputed from the remaining detectors. A dimension with every detector disabled disappears and isn't carried forward.
- Its existing issues are hidden from `status`, `show`, `next` and the score, like suppressed issues (pattern `disabled:<detector>`). Their status doesn't change, so a disabled detector isn't counted as a fix. Wontfix issues are left untouched.
- `config set` and `config unset` rescore the saved state immediately. After re-enabling, the next scan rechecks the hidden issues: those still present are open again (not reopened), and the rest are auto-resolved.
- `status` lists what is disabled and how many issues that hides. `show <detector>` says when the detector is disabled. `next` doesn't mention it, since nothing disabled is in the queue.

## Issue weights

An issue's **tier does not affect the score**. Its confidence does:

| Confidence | Weight |
|---|---|
| High | 1.0 |
| Medium | 0.7 |
| Low | 0.3 |

Some issues don't count at all:

- Suppressed issues (matched by an ignore pattern) are skipped.
- Issues outside the last scan's `--path` are skipped.
- Issues in the **test**, **config**, **generated** and **vendor** zones are skipped. Production and script files count. The script zone covers `scripts/`, `bin/`, `examples/` and `example/` directories; its files aren't expected to have tests, so Test health scores production files only. `tsconfig_health` reports on tsconfig files, so its issues count in the config zone too.

### File-based detectors

For smells, security, test_coverage, type_error, nextjs and next_lint (plus some excluded or unused ones, see `_FILE_BASED_POLICY_DETECTORS`), the potential is a number of files. A file's failures are capped so that one bad file can't dominate:

- 1–2 issues: up to 1.0;
- 3–5 issues: up to 1.5;
- 6 or more issues: up to 2.0.

`test_coverage` works differently. Each scorable file contributes `min(sqrt(LOC), 50)` to the potential, and its issues fail by at most that same weight. That makes a large untested file cost more than a small one. Files shorter than 10 lines aren't scored at all, so a fix that shrinks every remaining file below that leaves Test health with nothing to check, and the dimension drops out of the score.

### Type checks

`type_error` reports what tsc reports, read from the same tsc run as `unused` (`detectors/tsc.py` runs tsc once per scan with `--noUnusedLocals --noUnusedParameters --listFiles`; those flags only add the unused diagnostics, which `type_error` leaves out). Its potential is the number of files tsc checked in the scan path, from `--listFiles`. One issue covers one error code on one line, with ID `type_error::<file>::TS<code>::<line>`.

- tsc runs on the scan path's nearest tsconfig (`tsconfig.app.json`, then `tsconfig.json`), as for `unused`. A file whose own nearest tsconfig is a different one belongs to another project, such as a package in a monorepo, and is neither reported nor counted: a root config isn't the config that package is checked with. The scan records reduced coverage naming those tsconfigs; scanning that directory checks it.
- Compiler-option errors (TS5xxx, TS6xxx) aren't issues.
- A package or its types that can't be found (`Cannot find module 'x'` for a bare specifier, missing `@types`, missing JSX types) is an uninstalled dependency, not a code error. Such a file's errors aren't reported and the file isn't counted, since the rest may be cascades of the missing types. When the project declares dependencies and no `node_modules` exists above its tsconfig, the detector doesn't run at all.
- Errors that depend more on compiler options or ambient types than on the code (implicit `any`, index-signature access, module-format interop, unknown globals, an unused `@ts-expect-error`) have medium confidence; the rest have high confidence.

When tsc doesn't run (not installed, no tsconfig, dependencies not installed, a Deno project), the detector reports no potential: Type checks is carried forward and its open issues aren't auto-resolved.

## Subjective dimensions

A subjective dimension's score is the score from its latest imported assessment, clamped to 0–100. Review issues for the dimension are counted for display, but they don't change the score. A placeholder from a scan reset scores 0. A dimension's weight comes from its metadata (`base/subjective_dimensions.py`):

| Dimension | Weight |
|---|---|
| High elegance, Mid elegance | 22.0 each |
| Low elegance, Contracts, Type safety | 12.0 each |
| Design coherence | 10.0 |
| Abstraction fit | 8.0 |
| Logic clarity | 6.0 |
| Structure nav | 5.0 |
| Error consistency | 3.0 |
| Naming quality | 2.0 |
| AI generated debt, API coherence, Auth consistency, Convention drift, Cross-module arch, Dep health, Stale migration, Init coupling, Test strategy | 1.0 each |

Subjective dimensions are never carried forward.

## Lenient, strict and verified

The three modes differ only in which issue statuses count as failures. `issue_counts_as_failure` decides this, using `FAILURE_STATUSES_BY_MODE` and `SCAN_VERIFIED_PASSES_BY_MODE`:

| Status | Lenient | Strict | Verified |
|---|---|---|---|
| `open`, `deferred`, `triaged_out` | fails | fails | fails |
| `wontfix` | passes | fails until a scan confirms the finding is gone | fails until a scan confirms the finding is gone |
| `fixed`, `false_positive` | passes | passes | fails until a scan confirms the finding is gone |
| `auto_resolved` | passes | passes | passes |

- **Lenient** (overall, objective) counts only work you haven't done.
- **Strict** also counts debt you accepted with `wontfix`, for as long as the finding is still there. The gap between overall and strict is your wontfix debt. The debt totals in the scan summary, `status` and the narrative count the same issues (`is_wontfix_debt`, `stats.wontfix_debt`); `stats.wontfix` is the plain status count.
- **Verified** also counts a manual `fixed` or `false_positive` until a later scan confirms the finding is gone. It covers the mechanical dimensions only.

"Confirmed gone" means the issue's detector ran and no longer reports it, the zone policy now skips that detector for the file, or the file is gone. `dev/QUEUE_LIFECYCLE.md` in the repository gives the details.

- A confirmed `open`, `deferred` or `triaged_out` issue becomes `auto_resolved`. `auto_resolved` never counts.
- A confirmed `wontfix`, `fixed` or `false_positive` issue keeps its status, and the scan records `resolution_attestation.scan_verified` on it. A scan never changes a `wontfix` or `false_positive` status: if the finding comes back, the scan clears the mark and the issue counts again where its mode counts it. A `fixed` issue whose finding comes back is reopened.
- An absence that isn't confirmed changes nothing, so the mark stays as it was. That covers a detector that didn't run and a file outside the scan's `--path`.

**Fixing every finding and rescanning brings objective and verified to 100.** Overall and strict also need the subjective dimensions to be assessed.

## Score confidence

If a tool such as tsc or knip couldn't run fully, the scan records reduced coverage for the affected detectors. `state["score_confidence"]` and each affected dimension then carry a `coverage_status` of `reduced` (`state_coverage.py`). This only labels the score; it doesn't change the numbers.

## What the score does not measure

- Feature completeness, performance or user experience.
- Comparisons between codebases: 85 on a 500-file project and 85 on a 50-file project mean different things.

The score tracks improvement over time. It is not an absolute quality rating.
