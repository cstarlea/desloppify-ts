# Queue Lifecycle

This page covers how the work queue moves through a cycle, how issue statuses change, and what keeps state and plan consistent. The queue is built from `state.json` (issues and scores) and `plan.json` (queue order, skips, clusters, lifecycle markers). Scoring is in `docs/scoring.md`.

## Phases

### Persisted mode

`plan.refresh_state.lifecycle_phase` is either `"plan"` or `"execute"`. Only `reconcile_plan()` (`engine/_plan/sync/pipeline.py`) writes it, through `_set_lifecycle_phase()`. Older fine-grained names are mapped to one of the two at load time by `migrate_legacy_phase()` (`engine/_plan/refresh_lifecycle.py`).

### Display phase

Every queue build derives the display phase again from the live items. The display phase is never persisted. The flow is:

- `build_queue_snapshot()` (`engine/_work_queue/snapshot.py`) calls `_phase_for_snapshot()`, which calls `derive_display_phase()`.
- The first phase in the table below that has items wins.
- `user_facing_mode()` maps every phase except `execute` to `plan`.

| Phase | When | What `next` shows |
|---|---|---|
| `review_initial` | Fresh boundary (no `plan_start_scores`, or a reset) and unscored subjective dimensions | Initial subjective review. Objective work stays hidden |
| `scan` (preferred) | Lifecycle mode is `execute` (`current_lifecycle_phase`), nothing is queued for execution, and a scan-phase item exists | As for `scan` below |
| `assessment` | Subjective re-review items or assessment requests | Those items |
| `workflow` | Score checkpoint, import scores, communicate score, create plan | Those workflow items |
| `triage` | `triage::` stages | Triage stages |
| `review` | Review findings, once triage is current for them | Review findings |
| `execute` | Executable work | Objective issues in `queue_order`. If `queue_order` holds nothing at all, every unskipped objective issue (`executable_objective_ids`) |
| `scan` | Nothing above | `workflow::deferred-disposition` if any temporary skips exist, otherwise `workflow::run-scan` |

If execution work exists and the persisted mode is `execute`, the snapshot hides the assessment, workflow, triage and review items. Objective issues found mid-cycle go to the backlog, not the queue.

`reconcile_plan` derives the same phase from `queue_order` (`_resolve_reconcile_display_phase`). `test_phase_derivation_equivalence_matrix` keeps the two in step.

## The scan boundary

`refresh_state.postflight_scan_completed_at_scan_count` records that the current cycle's scan has run. While it's missing, `build_run_scan_item()` offers `workflow::run-scan`.

After a scan, `_mark_postflight_scan_completed_if_ready()` (`app/commands/scan/plan_reconcile.py`) sets the marker. It holds off in two cases:

- a cycle is running (`plan_start_scores` is set) and some subjective dimensions are still unscored;
- temporary skips remain (`build_deferred_disposition_item()`).

`invalidate_postflight_scan()` clears the marker when a command changes objective queue work: skip, unskip, backlog, resolve and others. A new scan is then due.

Temporary skips therefore **block the scan step** until one of these happens:

- `plan unskip` reactivates them;
- `plan backlog` drops them from the plan, and the issues reopen;
- `plan skip --permanent --deferred-only "*"` makes them wontfix (the item's suggested command; without `--deferred-only`, `"*"` would also wontfix every open issue);
- they resolve on their own. A deferred issue whose finding a scan confirms gone becomes `auto_resolved`, and reconcile supersedes its skip entry.

### After a scan

`reconcile_plan_post_scan()` runs these steps:

1. `reconcile_plan_after_scan()` (`engine/_plan/scan_issue_reconcile.py`), if the plan has any queue, skip, cluster or override content. It runs these sub-steps:
   - **Restore returned issues.** Superseded entries whose issue is actionable again are forgotten, so a fresh skip or queue entry survives.
   - **Sync skip status.** Skipped issues still `open` in state get the skip's status: `deferred`, `triaged_out` and so on.
   - **Supersede.** These entries move to `plan.superseded`:
     - plan references to issues no longer in state;
     - queue, promoted and cluster references to issues that aren't `open`, `deferred` or `triaged_out`;
     - skip entries whose issue is now `auto_resolved`.

     Wontfix and false-positive skip entries are kept, because those issues keep their status.
   - **Close clusters.** A cluster is marked done when it has emptied, or when all its issues are `fixed`, `auto_resolved` or `wontfix`.
   - **Resurface skips.** Temporary skips whose `review_after` scans have passed return to the queue, and their `deferred` issues reopen.
   - **Prune.** Superseded entries older than 90 days are dropped.
2. `reconcile_plan()` runs only at a boundary: when the live queue has nothing left but synthetic items, or on `--force-rescan`. It syncs subjective dimensions, auto-clusters, injects workflow and triage items, and writes the persisted mode. Other callers:
   - `plan resolve`, `skip`, `unskip`, `backlog` and `reopen` call it when `invalidate_postflight_scan()` cleared the marker;
   - a workflow-item resolve calls it when that drains the queue;
   - a review import calls it at a boundary.
3. Seed or clear `plan_start_scores`, then set the scan marker.

## Issue statuses

| Status | Set by |
|---|---|
| `open` | A new finding. Also set when a finding reappears, or by `plan unskip`, `plan reopen` or `plan backlog` |
| `fixed` | `plan resolve` (and autofix) |
| `wontfix` | `plan skip --permanent` (`--note` and `--attest` required) |
| `false_positive` | `plan skip --false-positive`. A triage-observe auto-skip becomes one at the next scan's reconcile |
| `deferred` | `plan skip` (temporary) |
| `triaged_out` | Triage dismissal |
| `auto_resolved` | A scan confirming the finding is gone |

The status mapping for skip kinds is `skip_kind_state_status()` in `engine/_plan/skip_policy.py`. Manual changes go through `resolve_issues()` in `engine/_state/resolution.py`. By default it only matches issues that are currently `open`, except when it is reopening them.

`plan skip` changes issues that are `open`, `deferred` or `triaged_out` (`SKIPPABLE_STATUSES`), so a deferred issue can later be made `wontfix` or `false_positive`. It leaves `wontfix`, `false_positive`, `fixed` and `auto_resolved` issues alone, in state and in the plan; to change one, reopen it first with `plan unskip` (`--force` for a skip with a note). `--deferred-only` restricts it to issues that already have a temporary skip.

### What a scan does to existing issues

`merge_scan()` (`engine/_state/merge.py`) calls `upsert_issues()` and then `verify_disappeared()` (`merge_issues.py`).

An absence is **confirmed** when one of these holds:

- the issue's detector ran this scan, meaning it reported a potential or any finding;
- the zone policy now skips the detector for that file;
- the file is gone.

Before any of that, the scan skips these issues:

- issues from another language;
- issues whose detector is suspect, i.e. it had open issues before and apparently didn't run this time (`find_suspect_detectors`);
- issues matching an exclusion.

Issues outside the scan's `--path` are never confirmed.

| Status before | Finding absent | Finding present again |
|---|---|---|
| `open`, `deferred`, `triaged_out` | `auto_resolved` once confirmed. The note records the old status | No change |
| `wontfix` | Stays `wontfix`. Once confirmed, it gets `resolution_attestation.scan_verified`, and its note is kept | Stays `wontfix`. `scan_verified` is cleared, so strict and verified count it again |
| `fixed`, `false_positive` | Stays as is. Marked `scan_verified` on any absence, even an unconfirmed one (see the known gaps) | Reopened as `open`, with `reopen_count` + 1 and the attestation dropped |
| `auto_resolved` | No change | Reopened as `open` |

A scan never changes a `wontfix` status. `scan_verified` is what lets a gone wontfix stop counting against strict and verified (`issue_counts_as_failure`). Separately, the scan adds a `stale_wontfix` work item for a wontfix whose finding is still present when either:

- `wontfix_decay_scans` scans (config, default 20) have passed since it was marked wontfix;
- a structural finding has grown by at least 10 complexity or 50 LOC.

Resolving that item as fixed restarts the clock.

## Persistence safety

### Locking

`cli.main()` wraps every command in `command_lock()` (`app/commands/helpers/command_lock.py`).

A command that may save holds the state lock and then the plan lock(s) from before its first load until it returns. They are taken in rank order, with a 600 s timeout. If it has to wait, it prints "Waiting for another desloppify command to finish...".

Three cases run without the command lock:

- **Read-only commands:** `status`, `show`, `next`, `backlog`, `detect`, `tree`, `viz`, `dev`, `move`, `setup` and `update-skill`.
- **`plan triage --run-stages`:** it spawns desloppify subprocesses that take the locks themselves.
- **`review --run-batches` and `review --scan-after-import`:** for the same reason.

The primitive is `exclusive_file_lock()` (`base/discovery/file_paths.py`). It is:

- re-entrant per thread;
- cross-process through `flock` or `msvcrt`;
- ranked: state 10, plan 20, progression 30. Taking a lower rank while holding a higher one raises `LockOrderError`.

### Quarantine and corrupt files

`load_state()` (`engine/_state/persistence.py`) moves a malformed work item to `state["quarantined_work_items"]` and loads the rest. `load_plan()` (`engine/_plan/persistence.py`, `schema/quarantine.py`) does the same for a malformed queue entry, skip, cluster or override, moving it to `plan["quarantined_entries"]`.

A file that can't be used is handled like this:

1. It is renamed to `.corrupted` (or `.corrupted.N`), and `.bak` is restored in its place.
2. If `.bak` is unusable too, the file starts fresh.
3. `.bak` isn't rotated again until a clean file has been written.

The rename and restore run under the file's lock, or in memory only if the lock stays busy for 5 s. A quarantined issue drops out of the scores at the next save, since every save recomputes them. A rescan that still finds the issue adds it back.

## Known gaps

These are tracked in `dev/FORK_ROADMAP.md` as 2.39 and 2.40.

- **`fixed` and `false_positive` are marked `scan_verified` on any absence.** That includes absences where their detector didn't run, and issues outside the scan's `--path`, so verified can credit a fix that no detector confirmed.
- **Clusters close only on `fixed`, `auto_resolved` and `wontfix`.** `_reconcile_active_clusters_by_item_status` doesn't count `false_positive`, so a cluster whose last open issue was marked a false positive stays active.
