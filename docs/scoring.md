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

## Two pools

Overall and strict blend two pools of dimensions (`MECHANICAL_WEIGHT_FRACTION`, `SUBJECTIVE_WEIGHT_FRACTION`):

| Pool | Share | Source |
|---|---|---|
| **Mechanical** | 25% | Detectors run by `scan` |
| **Subjective** | 75% | Assessments imported by `review` |

Each pool is a weighted average of its dimension scores.

The subjective pool always contains every default subjective dimension (20 for TypeScript). A dimension that hasn't been assessed scores 0, so **before the first review, overall and strict are at most 25**, which is a quarter of the mechanical average. Objective and verified don't use the subjective pool.

If no detector reports any checks and there are no assessments, all four scores are 100 (`_set_perfect_scores`).

## Mechanical dimensions

Each detector reports a **potential**, the number of checks it ran, along with its issues. A dimension pools the detectors in it that have a potential above 0:

    dimension_score = (checks - weighted_failures) / checks * 100

| Dimension | Weight in pool | Detectors that TypeScript scans emit |
|---|---|---|
| **File health** | 2.0 | structural |
| **Code quality** | 1.0 | unused, logs, exports, deprecated, smells, react, nextjs, next_lint, orphaned, flat_dirs, naming, single_use, coupling, facade, props, patterns, responsibility_cohesion, stale_exclude |
| **Duplication** | 1.0 | dupes, boilerplate_duplication |
| **Test health** | 1.0 | test_coverage |
| **Security** | 1.0 | security, cycles |

The detector-to-dimension mapping comes from the registry (`base/registry/catalog_entries.py`), and the weights come from `MECHANICAL_DIMENSION_WEIGHTS`. The registry also maps some detectors that no TypeScript phase emits, such as the Rust detectors. They never report a potential, so they never count. Review, concerns, `signature`, `stale_wontfix` and a few others create work items but are kept out of scoring (`SCORING_EXCLUDED_DETECTORS`).

### Sample dampening

A mechanical dimension with fewer than 200 checks (`MIN_SAMPLE`) has its weight reduced in proportion. For example, 50 checks count at 25% of the dimension's weight.

### Carried-forward dimensions

If a mechanical dimension is missing from a scan, its previous score is kept and marked `carried_forward` (`_materialize_dimension_scores`). This only happens when none of its detectors reported a potential, meaning none of them ran. If a detector ran and found nothing left to check, the old score is dropped.

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
- Issues in the **test**, **config**, **generated** and **vendor** zones are skipped. Production and script files count.

### File-based detectors

For smells, security, test_coverage, nextjs and next_lint (plus some excluded or unused ones, see `_FILE_BASED_POLICY_DETECTORS`), the potential is a number of files. A file's failures are capped so that one bad file can't dominate:

- 1–2 issues: up to 1.0;
- 3–5 issues: up to 1.5;
- 6 or more issues: up to 2.0.

`test_coverage` works differently. Each scorable file contributes `min(sqrt(LOC), 50)` to the potential, and its issues fail by at most that same weight. That makes a large untested file cost more than a small one.

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
- **Strict** also counts debt you accepted with `wontfix`, for as long as the finding is still there. The gap between overall and strict is your wontfix debt.
- **Verified** also counts a manual `fixed` or `false_positive` until a later scan stops reporting it. It covers the mechanical dimensions only.

"Confirmed gone" is recorded on the issue as `resolution_attestation.scan_verified`. A scan never changes a `wontfix` status. When the finding disappears, the scan sets the mark, and if the finding comes back, it clears the mark and the issue counts again. `auto_resolved` means a scan confirmed the finding is gone, so it never counts. When a scan confirms an `open`, `deferred` or `triaged_out` issue is gone, it becomes `auto_resolved`. `dev/QUEUE_LIFECYCLE.md` in the repository says exactly what counts as confirmation.

**Fixing every finding and rescanning brings objective and verified to 100.** Overall and strict also need the subjective dimensions to be assessed.

## Score confidence

If a tool such as tsc or knip couldn't run fully, the scan records reduced coverage for the affected detectors. `state["score_confidence"]` and each affected dimension then carry a `coverage_status` of `reduced` (`state_coverage.py`). This only labels the score; it doesn't change the numbers.

## What the score does not measure

- Feature completeness, performance or user experience.
- Comparisons between codebases: 85 on a 500-file project and 85 on a 50-file project mean different things.

The score tracks improvement over time. It is not an absolute quality rating.
