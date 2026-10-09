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
| **Code quality** | 1.0 | unused, logs, exports, dependencies, deprecated, smells, react, nextjs, orphaned, flat_dirs, naming, single_use, coupling, cycles, facade, props, patterns, responsibility_cohesion, stale_exclude, tsconfig_health |
| **Duplication** | 1.0 | dupes, boilerplate_duplication |
| **Test health** | 1.0 | test_coverage |
| **Security** | 1.0 | security |
| **Type checks** | 1.0 | type_error |
| **Lint** | 1.0 | lint |

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

For smells, security, test_coverage, type_error, lint and nextjs (plus some excluded or unused ones, see `_FILE_BASED_POLICY_DETECTORS`), the potential is a number of files. A file's failures are capped so that one bad file can't dominate:

- 1–2 issues: up to 1.0;
- 3–5 issues: up to 1.5;
- 6 or more issues: up to 2.0.

`test_coverage` works differently. Each scorable file contributes `min(sqrt(LOC), 50)` to the potential, and its issues fail by at most that same weight. That makes a large untested file cost more than a small one. Files shorter than 10 lines aren't scored at all, so a fix that shrinks every remaining file below that leaves Test health with nothing to check, and the dimension drops out of the score.

Without a coverage report, a file's verdict comes from the import graph. A file a test imports is directly tested, and only the quality of those tests can fail it (`shallow_tests`, `snapshot_heavy` and so on). A file reached only through tested modules is `transitive_only`, unless a tested public entry (package.json `exports`, `main` and so on) reaches it. Any other file is `untested_module`, or `untested_critical` with 10 or more importers or high complexity.

When the project has a coverage report, measured line coverage takes over from the graph, file by file. The scan reads `coverage/coverage-final.json` (Istanbul) or `coverage/lcov.info` in the project root and in each package directory, plus a `reportsDirectory` (vitest) or `coverageDirectory` (jest) that a config file names. Run the tests with coverage before scanning; the scan doesn't run them.

- A file at or above 80% of lines covered passes, whatever the graph said.
- A file below 80% gets `low_coverage` (ID `test_coverage::<file>::low_coverage`). It fails by its √LOC weight times the share of the target it misses, `1 − pct/80`, so 40% costs half the weight and 79% almost nothing. Branch coverage is shown but not scored.
- A file whose lines all show zero hits never ran under the tests. It gets `untested_module` or `untested_critical`, the same IDs the graph gives.
- Quality checks on a file's direct tests still apply.
- A file that changed after the report was written (a newer modification time), or whose report lines run past the end of the file, isn't measured: it keeps the graph's verdict, and the scan log counts it.
- A file missing from every report also keeps the graph's verdict. Coverage tools leave out both files that never loaded and files excluded on purpose, and the report can't tell the two apart.
- Several reports are merged by covered line, so per-package reports in a monorepo and a root report from another run add up.

### File health

`structural` reports at most one issue per file (`structural::<file>`), listing every signal the file hits: large (500 lines or more), a complexity score, a god component (React hook counts), god classes and mixed concerns. Three or more signals make the issue tier 4 and high confidence; otherwise it is tier 3 and medium. A file whose only signal is complexity needs a score of 50.

A class is a god class when it meets two of these rules:

- 20 or more methods. Arrow-function fields count; accessors and overload signatures don't.
- 7 or more constructor dependencies: constructor parameters plus fields set with Angular's `inject()`.
- 40 or more decorators on the class, its methods and their parameters. Field decorators (ORM columns, validators, Angular inputs) declare a shape, so they don't count.
- 300 or more lines.

One rule alone isn't enough. NestJS services and Angular components routinely inject several collaborators, and a controller stacks decorators on every route. The classes are listed in the issue's `detail.god_classes` (`desloppify detect gods` shows them).

### Type checks

`type_error` reports what tsc reports, read from the same tsc run as `unused` (`detectors/tsc.py` runs tsc once per scan with `--noUnusedLocals --noUnusedParameters --listFiles`; those flags only add the unused diagnostics, which `type_error` leaves out). Its potential is the number of files tsc checked in the scan path, from `--listFiles`. One issue covers one error code on one line, with ID `type_error::<file>::TS<code>::<line>`.

- tsc runs on the scan path's nearest tsconfig (`tsconfig.app.json`, then `tsconfig.json`), as for `unused`. A file whose own nearest tsconfig is a different one belongs to another project, such as a package in a monorepo, and is neither reported nor counted: a root config isn't the config that package is checked with. The scan records reduced coverage naming those tsconfigs; scanning that directory checks it.
- Compiler-option errors (TS5xxx, TS6xxx) aren't issues.
- A package or its types that can't be found (`Cannot find module 'x'` for a bare specifier, missing `@types`, missing JSX types) is an uninstalled dependency, not a code error. Such a file's errors aren't reported and the file isn't counted, since the rest may be cascades of the missing types. When the project declares dependencies and no `node_modules` exists above its tsconfig, the detector doesn't run at all.
- Errors that depend more on compiler options or ambient types than on the code (implicit `any`, index-signature access, module-format interop, unknown globals, an unused `@ts-expect-error`) have medium confidence; the rest have high confidence.

When tsc doesn't run (not installed, no tsconfig, dependencies not installed, a Deno project), the detector reports no potential: Type checks is carried forward and its open issues aren't auto-resolved.

### Lint

`lint` runs the project's own linter with the project's own config (`detectors/lint/`) and reports what it finds: ESLint, XO (which wraps ESLint), Biome or oxlint. Its potential is the number of files linted in the scan path. One issue covers one rule on one line, with ID `lint::<file>::<rule>::<line>`; the detail keeps the columns, count, severity, message and whether the linter can fix it.

- The linters are the ones configured in the nearest directory at or above the scan path: `eslint.config.*`; `xo.config.*`, `.xo-config*`, or `xo` in package.json (as a key or a dependency); `biome.json(c)` unless its `linter.enabled` is false; `.oxlintrc.json`. Each one configured there runs from that directory, so a project that pairs oxlint with ESLint gets both. `.eslintrc*` and a package.json `eslintConfig` count only when no other config is above the scan path, since ESLint ignores them once a flat config is in effect.
- A directory under the scan path with its own `eslint.config.*` belongs to another project, such as a package in a monorepo, and is neither linted nor counted; the scan records reduced coverage naming it, and scanning that directory lints it. Nested Biome and oxlint configs extend the root one, so they aren't boundaries.
- The linter is the project's `node_modules/.bin/<linter>`, never a global install or `npx`. With no project linter configured, the detector does nothing.
- Rule IDs are ESLint's (`@typescript-eslint/no-floating-promises`). oxlint's codes are renamed to match (`typescript(no-explicit-any)` is `@typescript-eslint/no-explicit-any`), so a rule that oxlint and ESLint both report on a line is one issue. Biome's are its category without `lint/` (`suspicious/noDoubleEquals`).
- Formatting rules (`meta.type: "layout"`, `@stylistic/*`, `prettier/*`) aren't issues. Neither are rules that check what a desloppify detector already reports, so the same problem isn't counted twice: unused variables and imports (`unused`), `no-explicit-any`, `ban-ts-comment`, `no-non-null-assertion`, `no-empty`, `require-await`, `no-magic-numbers`, `default-case`, `no-warning-comments`, `complexity`, `max-lines-per-function` (smells), `max-lines` (structural), `no-eval`, `no-new-func`, `react/no-danger` (security), and Biome's equivalents. The list is `DUPLICATED_BY` in `detectors/lint/rules.py`.
- Confidence comes from the rule. ESLint's `problem` rules are high and `suggestion` rules medium; Biome's correctness, suspicious and security groups and oxlint's correctness and suspicious categories count as `problem`, their style and nursery rules as stylistic. Naming, ordering and file-name conventions, and typescript-eslint's stylistic rules, are low. The type-aware typescript-eslint rules have their own table: async and comparison bugs (`no-floating-promises`, `no-misused-promises`, `await-thenable`, `switch-exhaustiveness-check`, ...) are high, the `no-unsafe-*` family and `restrict-template-expressions` are medium (an untyped dependency or compiler options let `any` in), and cleanups (`no-unnecessary-type-assertion`, `prefer-nullish-coalescing`, ...) are low. A rule set to `warn` is one step lower. Severity `error` alone doesn't make a finding high.
- A file the linter can't parse isn't counted; reduced coverage says how many. Biome and oxlint don't list the files they linted, so for them the potential is the scan's files in the scan path.

A type-aware ESLint or XO config (`parserOptions.project` or `projectService`, read with `--print-config`) builds a TypeScript program per tsconfig, which is slow and memory-hungry: trpc's `packages/` (637 files) ran out of Node's 2 GB heap after 50 s. Such a run is skipped when the scan has more than `languages.typescript.lint_type_aware_max_files` files to lint (400 by default; 0 means no limit).

When a linter doesn't run (not installed, dependencies not installed, a config or plugin it can't load, a crash, out of memory, the 300 s timeout, the size limit), it is named in reduced coverage. When none runs, the detector reports no potential: Lint is carried forward and its open issues aren't auto-resolved.

`lint` replaces the Next.js `next_lint` tool phase, which ran `next lint` (removed in Next.js 16) and kept only a per-file count. A scan that runs ESLint reports `next_lint` as having run, so its open issues from older scans resolve.

### Knip

Knip runs once per scan (`detectors/knip_adapter.py`, cached like tsc), and only when the project installs it: desloppify never downloads it. In a monorepo it runs from the workspace root, with `--workspace` when the scan path is one package. Three detectors read the run, all under Code quality:

- `exports` reports Knip's unused exports and types (`exports::<file>::<name>`), unused enum members (`exports::<file>::<Enum>.<Member>`) and duplicate exports, one name exported under several (`exports::<file>::<a>=<b>`, tier 3, medium confidence). A deprecated alias isn't a duplicate (it's kept on purpose, and `deprecated` reports it), nor is an alias already reported as an unused export.
- `orphaned` uses Knip's unused files as corroboration and reports nothing new. An orphan Knip also reports goes from medium to high confidence, unless an import Knip can't resolve either may point at it. An orphan Knip doesn't report is a file it reaches from an entry point it knows (a plugin's config, a manifest field) or one its config ignores, so it drops to low confidence.
- `dependencies` checks the manifests (`package.json`) nearest the scanned sources, inside the scan path. Its potential is the number of dependencies and devDependencies they declare, plus the number of unlisted packages. IDs are `dependencies::<package.json>::<kind>::<package>`:
  - `unused`, a dependency nothing uses (tier 2, high confidence), and `unused_dev`, the same for a devDependency (tier 3, medium confidence, since tools Knip has no plugin for use them);
  - `unlisted`, a package imported but not declared, one issue per manifest and package (tier 2; high confidence when a production or script file imports it, medium when only tests or config do);
  - `unlisted_binary`, a binary a manifest script runs without declaring its package (tier 3, medium confidence). Binaries Knip finds in source code are left out; they're usually system commands.

  A dependency that a scanned file still imports isn't reported unused: Knip calls it unused because every file importing it is unused, and those files are the finding. Nor is one whose name or binary a manifest script mentions, or that is configured under its own manifest key (`"lint-staged": {...}`): Knip doesn't see through task runners it doesn't know (`nub exec --node husky`). When a manifest's dependencies aren't all installed, its unused dependencies and binaries aren't checked, because Knip loads plugin configs and finds binaries through `node_modules`; the scan records reduced coverage naming the manifests.

Knip's unresolved imports aren't reported. In TypeScript files tsc reports them (`type_error`, TS2307), and Knip reports a bare package it can't find as unlisted. They only keep an orphan at low confidence. Knip's namespace members, catalog entries, optional peer dependencies and cycles aren't read either: `cycles` has its own detector, and namespace members flag the members of published type namespaces.

When Knip doesn't run (not installed, a crash, no JSON report), `exports` and `dependencies` record reduced coverage. `dependencies` reports no potential, so its open issues aren't auto-resolved, and orphans keep their own confidence.

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
