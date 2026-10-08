# desloppify fork roadmap

_Last updated 2026-10-08. Originally built from a multi-agent review of upstream v1.0 (`3a7735d5`) on 2026-10-05; the appendix keeps that review's findings with their current status._

The fork (`cstarlea/desloppify`) is a **TypeScript/JavaScript-only** code-health scanner. Upstream (`peteromallet/desloppify`) is unmaintained and is treated as read-only: something we may cherry-pick from, nothing more. The last multi-language commit is tagged `pre-ts-only`.

---

## 1. Where things stand

**Scope.** One language plugin, `languages/typescript/`, scans `.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs` and `.cjs`. The 28 other plugins, the generic tool and tree-sitter plugin layers, `--lang`, auto-detection, and plugin discovery/registration are all gone. That cut about 55k lines. `languages.framework.default_lang()` builds the TypeScript config directly, and state lives in one `.desloppify/state.json`. Legacy `state-typescript.json` and `state-javascript.json` files are migrated on first run.

**Accuracy.** The resolver and graph work (Milestones 0 and 1) removed most of the false positives that made upstream v1.0 untrustworthy on real repos:

- Every fixer (unused-imports, unused-vars, unused-params, debug-logs, empty-if-chain and dead-useeffect) edits syntax-tree nodes and runs without `--unsafe`; no fixer is marked unsafe now (2.3, #32). Every fixer and `move` refuses to write output that parses worse than its input, and round-trip property tests cover every fixer (#30).
- tsc and knip run correctly and report reduced coverage instead of silent zeros.
- Imports come from the tree-sitter syntax tree.
- One `ModuleResolver` handles tsconfig `paths`/`extends`/`references`, workspaces and package `exports`, and is shared by the graph, test coverage and `move`.
- Entry points come from package.json and from Next.js and React Router conventions, checked per package.
- Results no longer depend on the directory the scan runs from.

**Measurement.** These guard regressions:
- **Golden fixtures** (`languages/typescript/tests/golden/`): vite-react, next-app, node-lib and pnpm-monorepo. Each has `must_find`/`must_not_find` expectations and strict known-false-positive lists. They run in two layers: hermetic, and node with pinned tsc and knip.
- **Real repos:** the issue counts on ky, zod, trpc and vercel/commerce (currently 146 / 686 / 1124 / 79; the facade rewrite in #22 added 9 true positives). Every PR is diffed against these.

**CI.** These jobs run on every PR: lint, typecheck, arch-contracts, ci-contracts, tests-core, tests-full, tests-golden-node and package-smoke. Publishing to PyPI and release tweets are switched off behind repository variables.

### Merged PRs

| PR | What |
|---|---|
| #1 | M0: upstream batch (#617, #744, #760, #629, #750); fixers gated behind `--unsafe`; tsc/knip/tree-sitter failures reported as reduced coverage; tsconfig JSONC/`extends`/`references`; assertion patterns; Next 16 conventions; zones; security regex fixes |
| #2 | Golden TS fixture projects and the node CI job |
| #3 | Tree-sitter import extraction, type-only edges kept out of cycles, dynamic imports as edges, TypeScript resolution candidates (1.3, 1.4, 1.6) |
| #4 | Green CI on the fork; PyPI publish and release tweets gated |
| #5 | Workspaces, package.json entry points, per-package framework detection (1.2, 1.5) |
| #6 | Scans don't depend on the cwd; `--path` sets the project root (1.9) |
| #7 | Lower orphan confidence when an unresolved import may reach the file (1.7) |
| #8 | One `ModuleResolver` for the graph, test coverage and `move` (1.8) |
| #9, #10 | TS-only: drop every other plugin; the TS plugin scans JavaScript |
| #11 | Ports of upstream #817/#818 (Next.js `'use client'` rules in test files, `eval(` in JSDoc prose) |
| #12 | Remove the generic tree-sitter and tool-plugin layers |
| #13 | Single `state.json` with legacy migration; drop `--lang`, auto-detect, `langs`, `scaffold-lang`, `--by-language` |
| #14 | Replace the plugin registry with a direct TypeScript config |
| #15 | This roadmap |
| #16 | Syntax gate for fixers and `move`; real `--dry-run` diffs (2.2) |
| #17 | Shared tree-sitter parsing helper; AST unused-imports fixer, no longer unsafe (2.1, 2.3) |
| #18 | Autofix resolves exactly the issue IDs it fixed (2.5) |
| #19 | AST unused-vars fixer, no longer unsafe (2.3) |
| #20 | AST unused-params fixer, no longer unsafe (2.3) |
| #21 | Commands with `--path` default to the last scan's path (2.25) |
| #22 | Facade detector on the syntax tree (2.9) |
| #23 | AST debug-logs fixer removes standalone statements only, no longer unsafe (2.3) |
| #24 | AST empty-if-chain fixer, no longer unsafe (2.3) |
| #25 | Resilient `state.json` loading: quarantine, `.corrupted`, no `.bak` rotation after a bad load, version coercion (2.19) |
| #26 | Unused findings categorised on the syntax tree, with a `params` category (2.13) |
| #27 | Fixers resolve a lone pattern element that tsc reports at the pattern's `{` (2.3) |
| #28 | Params fixer renames every name of an "(all destructured elements)" pattern (2.3) |
| #29 | Resilient `plan.json` loading, the plan counterpart of #25 (2.19) |
| #30 | Fixer round-trip property tests; fixes for mixed line endings in `fixer_io` and `byte_offset` after CR/U+2028/U+2029 (2.4) |
| #31 | Roadmap catch-up for #20–#30 |
| #32 | AST dead-useeffect fixer, the last line-based fixer (2.3, FX-17) |
| #33 | `dead_useeffect` smell on the syntax tree; reports `function () {}` and bare `return;` callbacks (2.11) |
| #34 | Unused-vars fixer removes nested destructuring patterns that end up empty; one `ALL_DESTRUCTURED` constant (2.26, 2.27) |
| #35 | State and plan read-modify-writes under the locks; corrupt-file recovery under the lock; atomic progression trim (2.20) |
| #36 | Facade detector exempts files with `'use client'`/`'use server'` in the directive prologue (2.28) |
| #37 | package.json `imports` (`#subpath`) in the resolver; the vite-react `analytics.ts` false positive is gone (2.14) |
| #38 | Fix and rescan reaches 100 in every mode: strict ignores `auto_resolved`, verified credits a scan-confirmed `fixed`/`false_positive`, no carry-forward for a dimension whose detectors ran (2.18) |
| #39 | Next.js entry conventions move to `NEXTJS_SPEC.entry_conventions`; behaviour unchanged (2.15) |
| #40 | Shared typed syntax-tree helper: `parsed_file` parses once per scan; typed queries for functions, classes, imports/exports, JSX and calls; facade and `dead_useeffect` migrated (2.1) |
| #41 | `tree`/`viz` label the root node with the scanned path (2.31) |
| #42 | Scan-confirmed absence auto-resolves deferred and triaged_out issues and marks wontfix `scan_verified`; reconcile supersedes their skip entries (2.21) |
| #43 | CLI logging: `  WARNING: …` lines on stderr, coloured on a terminal, `DESLOPPIFY_LOG_LEVEL` (2.32) |

---

## 2. What's next, in priority order

Effort: S < 1 day, M a few days, L 1–2 weeks, XL multi-week. IDs in brackets refer to the appendix.

### 2A. AST-based fixers: remove the `--unsafe` gate (Milestone 2)

Every adversarial input in the original review broke one of the line-regex fixers. All six now edit syntax-tree nodes and none needs `--unsafe` (#17, #19, #20, #23, #24, #32). The flag and gate stay for future fixers.

| # | Item | Effort | Findings |
|---|---|---|---|
| 2.1 | **Done (#40).** Shared typed TS AST helper: `syntax.tree.parsed_file` parses each file once per scan through the parse cache (grammar by extension); `syntax.queries` returns dataclasses for functions (incl. overload signatures), classes and members, imports, exports, JSX elements and calls. Facade and the `dead_useeffect` smell use it; regex stays only as each caller's fallback without tree-sitter. Cohesion and `deps/imports` still parse .ts files with `tsx`, and cohesion keys the cache by relative path, so those parses aren't shared yet (→ 2.33) | L | AR-2 |
| 2.2 | **Done (#16)**, except `--verify`. Output validation gate for every fixer and `move`: parse before and after, refuse to write if the parse-error count rises, and show a real unified diff in `--dry-run`. Optional `--verify` runs `tsc --noEmit` and reverts on new errors | M | FX-11 |
| 2.3 | **Done for unused-imports (#17), unused-vars (#19), unused-params (#20, #27, #28), debug-logs (#23), empty-if-chain (#24) and dead-useeffect (#32); none needs `--unsafe`.** Every fixer now edits syntax-tree nodes. dead-useeffect removes only a standalone `useEffect`/`React.useEffect` statement with an empty, comment-free callback and deps that only read values; it leaves template strings, code sharing the line and the `//` line above alone (FX-17) | M | FX-17 |
| 2.4 | **Done (#30).** Fixer round-trip property tests: output parses, a second run is a no-op, CRLF/BOM/mode are preserved, and no new `tsc` errors appear. Seeded with the adversarial cases in the appendix. They found and fixed mixed-line-ending rewrites in `fixer_io` and `byte_offset` miscounting lines after CR/U+2028/U+2029 | M | FX-19 |
| 2.5 | **Done (#18).** Fixers return the exact issue IDs they fixed, so autofix resolves the right issues | S | FX-16 |
| 2.26 | **Done (#34).** unused-vars: nested patterns. An emptied inner pattern (`b` unused in `const { a: { b } } = o`) removes the pair or element holding it, cascading outward to the declarator, which still goes only with a pure initializer. TS6198 on a nested pattern cascades the same way. An array pattern whose elements all go is removed whole; a single array element still isn't | S | FX-8 |
| 2.27 | **Done (#34).** The "(all destructured elements)" name is defined once, as `ALL_DESTRUCTURED` in `syntax/nodes.py`, and imported by `detectors/unused.py`, `fixers/params.py` and `fixers/vars.py` | S | — |
| 2.33 | Share the remaining parses (#40 follow-up): cohesion keys the parse cache by relative path while `deps/imports` uses the resolved path, and both parse `.ts` with the tsx grammar, so neither shares `parsed_file`'s parse | S | AR-2 |

### 2B. Detector accuracy (rest of Milestone 2, plus M0 and M1 leftovers)

| # | Item | Effort | Findings |
|---|---|---|---|
| 2.6 | **Done (#47).** The function extractor and the five function-shape smells read `syntax.queries.definitions()`: declarations and named function expressions, variable-bound or assigned functions, and class members (`Owner.member`), so async, default exports, methods, multi-line/destructured params and concise arrows are seen and bodies come from the tree. `detector_flow` builds the list once per file; `async_no_await` looks for `await` in the function's own body. Signature variance skips default exports and uppercase route-handler names. Regex stays only as the fallback without tree-sitter | M | DT-3 |
| 2.7 | **Done (#54).** Type-safety smells read the syntax tree (`detector_types`): every non-null `!` (not just `x!.y`), `as unknown as T` / `<T><unknown>x` (`double_cast`), `any` in any type position (`keyof any` left out; `as any` / `<any>x` stay `as_any_cast`), and `@ts-ignore` / `@ts-expect-error` by TypeScript's own directive rules, block comments included. An `@ts-expect-error` with no reason after it or in the comment above is its own low-severity smell (`ts_expect_error_undocumented`); explained ones aren't reported. Each per-file issue carries its density (matches per 1,000 lines). Regex stays only as the fallback without tree-sitter | M | DT-9 |
| 2.8 | Props detector: count properties; include extends, generics and intersections; match names on word boundaries | M | DT-4 |
| 2.9 | **Done (#22).** Facade: every top-level statement is a re-export (directives and comments allowed); cover multi-line, `export * as`, `export type *` | S | DT-6 |
| 2.10 | Deprecated: attach JSDoc to the AST node; count importers from the graph plus uses in the same file; skip `.d.ts` | M | DT-5 |
| 2.11 | **Partial: the `dead_useeffect` smell is on the syntax tree, with a fallback that skips template and block-comment lines (#33).** Skip comment and string spans in the remaining line-regex detectors (security, smells, logs) | M | DT-7, FX-4 |
| 2.12 | Zones: `@generated` headers; directory-level issues classified by zone | S | DT-11 |
| 2.13 | **Done (#26).** A separate `params` category for unused symbols, with every category decided on the syntax tree | S | FX-15 |
| 2.14 | **Done (#37).** package.json `imports` (`#subpath`) in the resolver: the importer's nearest package.json is the scope; exact and `*` keys, condition objects and fallback arrays in order; bare targets resolve when they name a workspace package. The vite-react golden's `analytics.ts` false positive is gone | S | GR-1 |
| 2.15 | **Done (#39).** The `_NEXTJS_*` constants left `engine/detectors/orphaned.py` for `NEXTJS_SPEC.entry_conventions`; the language passes the specs' conventions to the detector through `OrphanedDetectionOptions`, since detectors may not import the language layer. Behaviour unchanged (commerce issue IDs identical) | S | GR-6 |
| 2.16 | **Done (#45).** Test coverage follows each imported name through re-export chains of any depth (`export { } from`, `export *`, `export * as ns`, import-then-export) to the file that defines it, via `syntax.queries`; namespace imports follow the members the test uses; type-only names aren't followed. trpc `parseTRPCMessage.ts` is directly tested. The one-hop name-blind barrel and facade expansions remain | M | DT-12 |
| 2.17 | Test-health score: count coverage through a tested public entry as covered, and fix the "production files" and "checks" labels | M | DT-12 |
| 2.28 | **Done (#36).** A file whose directive prologue holds `'use client'` or `'use server'` (after `'use strict'` or comments too) is not a facade, even if it only re-exports: in Next.js it marks a client or server boundary. None appear in the four repos | S | DT-6 |
| 2.29 | Line numbers in the logs and smells detectors: they split lines with `str.splitlines()`, which also breaks at U+2028/U+2029, CR, VT, FF and U+0085, while the debug-logs and empty-if-chain fixers match on tree-sitter rows, which count only LF. After such a character the fixer looks at the wrong row. #30 fixed the same class of bug for tsc positions in `byte_offset` | S | — |
| 2.34 | A `#x` import whose target is an npm package (not a workspace package) still counts as unresolved; it should count as external like a bare import (#37 follow-up) | S | GR-1 |
| 2.35 | `detect orphaned` reports `next.config.ts` as orphaned while `scan` does not, and the tree output says "1 files" (seen while checking #39) | S | GR-6 |
| 2.36 | The Next.js entry conventions check `next.config.{js,mjs,ts}`, while the framework detection list also has `next.config.cjs` (#39 kept the old list on purpose) | S | GR-6 |

### 2C. Engine and state correctness

| # | Item | Effort | Findings |
|---|---|---|---|
| 2.18 | **Done (#38).** Strict no longer counts scan-confirmed resolutions (`auto_resolved`) as failures; verified counts a manual `fixed`/`false_positive` once a rescan confirms it (`scan_verified`); a mechanical dimension whose detector ran with zero checks left is no longer carried forward with its old score. A test checks that fixing and rescanning reaches 100 in every mode; scoring.md and SKILL.md describe the three modes as the code does | S | CE-2 |
| 2.19 | **Done for `state.json` (#25) and `plan.json` (#29).** Resilient loading: quarantine invalid issues and plan entries instead of discarding the whole file; rename the bad file to `.corrupted`; don't rotate `.bak` after a failed load; coerce the version field | S | CE-3 |
| 2.20 | **Done (#35).** Every mutating command holds the state lock, then the plan lock, from its first load to its return; read-only commands load unlocked, and the corrupt-file rename and `.bak` restore run under the lock (or in memory if it stays busy). One re-entrant, ranked file lock backs `state_lock`, `plan_lock` and the progression log, whose trim now runs under the append lock. `plan triage --run-stages` and `review --run-batches`/`--scan-after-import` stay unlocked because they wait on desloppify subprocesses | M | CE-4 |
| 2.21 | **Done (#42).** Deferred and triaged_out issues auto-resolve when a scan confirms they're gone, under the same conditions as open issues, and reconcile supersedes their skip entries. Wontfix stays wontfix: the scan marks it `scan_verified`, so it stops failing strict and verified, and clears the mark if the finding returns; its skip entry is kept. A superseded entry is dropped once its issue reappears so a fresh skip or queue entry isn't stripped | S | CE-5 |
| 2.22 | **Done (#44).** `docs/scoring.md` (and its bundled copy) and `dev/QUEUE_LIFECYCLE.md` rewritten from the code, citing the functions behind each rule; the lifecycle doc lists the gaps found as 2.38–2.40 | S | CE-6 |
| 2.23 | First run: headline the objective score (marked provisional) until subjective dimensions are assessed; `--profile ci` prints plain output with a threshold exit code; move `cycles` out of the Security dimension | M | CE-12 |
| 2.24 | Expire carried-forward subjective dimensions; concerns ignore suppressed issues | S | CE-9, CE-10 |
| 2.25 | **Done (#21).** Commands with `--path` (autofix, detect, …) default to the last scan's path, as `review` already does, and fall back to `src/` only without one. A bare `scan` re-scans the last scope too | S | — |
| 2.30 | Plan quarantine coverage: #29 checks the entries of `queue_order`, `skipped`, `clusters` and `overrides`, but only the container type of `superseded`, `execution_log`, `commit_log` and `promoted_ids`. A malformed entry in those still loads as is | S | CE-3 |
| 2.31 | **Done (#41).** `tree` and `viz` label the root node with the scanned path relative to the project root (the project's directory name for a whole-project scan) and strip only that prefix, so `--path .` no longer merges `src/` into the root; `--focus` takes scan- or project-relative paths | S | — |
| 2.32 | **Done (#43).** `cli.main()` configures logging once: a stderr handler on the `desloppify` logger prints `  WARNING: message`, yellow (red for errors) on a terminal unless `NO_COLOR` is set. No global verbosity flag exists, so `DESLOPPIFY_LOG_LEVEL` (e.g. `DEBUG`) sets the threshold | S | — |
| 2.37 | Wontfix debt totals in the stats and `status` still include wontfix issues a scan confirmed gone (`scan_verified`), though the scores exclude them (#42 follow-up) | S | CE-5 |
| 2.38 | `plan skip --permanent` on a deferred issue leaves its state status `deferred`: `resolve_issues()` only matches `open` issues, so the plan entry becomes permanent but the issue keeps failing lenient. The deferred-disposition item suggests this exact command (found in the 2.22 pass) | S | CE-5 |
| 2.39 | `verify_disappeared` marks a `fixed`/`false_positive` issue `scan_verified` on any absence, including when its detector didn't run or the file is outside `--path`, so verified can credit an unconfirmed fix. Open, deferred, triaged_out and wontfix need a confirmed absence (found in the 2.22 pass) | S | CE-2 |
| 2.40 | Reconcile marks an active cluster done when all its issues are `fixed`, `auto_resolved` or `wontfix`, but not `false_positive` (found in the 2.22 pass) | S | — |

### 2D. New capabilities (Milestone 3)

| # | Item | Effort | Findings |
|---|---|---|---|
| 3.1 | **tsc `type_error` detector**: run tsc once and share its output with unused; stable IDs `TS####::line`; scored under type safety | M | TL-3 |
| 3.2 | **ESLint/Biome/oxlint**, chosen by which config is present; keep `ruleId` and severity; tier the type-aware typescript-eslint rules | M | TL-4 |
| 3.3 | **`tsconfig_health`**: strict off, missing `noUncheckedIndexedAccess`/`noImplicitOverride`/`verbatimModuleSyntax`, drift between packages; feed strictness into the review context | S | DT-13 |
| 3.4 | Knip's other categories: unused files (cross-checked with orphaned), unused/unlisted dependencies, unresolved imports, enum members, duplicates | M | TL-2 |
| 3.5 | Replace the original author's project-specific heuristics with config and presets: pattern families, boundary rules, data-layer identifiers, a `supabase` FrameworkSpec, public-env-secret prefixes (`NEXT_PUBLIC_`, `VITE_`, `PUBLIC_`, `EXPO_PUBLIC_`) | M | DT-10 |
| 3.6 | Framework specs in user-base order: React Router v7/Remix (beyond entry conventions), SvelteKit, Nuxt, Astro, NestJS, Express/Hono/Fastify, Angular. Each gets detection, `entry_conventions` and 2–4 high-signal scanners | XL | DT-14 |
| 3.7 | `<script lang="ts">` in `.vue`/`.svelte`/`.astro`, extracted as virtual files with line offsets | M | DT-14 |
| 3.8 | Backend security: unauthenticated server actions and route handlers, raw-SQL APIs with interpolation, `child_process` with template literals | M | DT-15 |
| 3.9 | Class-level god rules (methods, constructor-injected deps, decorators) | M | DT-17 |
| 3.10 | TS review overrides for type_safety, dependency_health and test_strategy; split the review guidance into React and Node sections | S | DT-16 |
| 3.11 | Ingest real coverage (`coverage-final.json`, `lcov.info`) when present | M | DT-12 |
| 3.12 | Detector and domain **disable** in config, which removes them from scoring instead of suppressing their issues | M | CE-15 |

### 2E. Engineering foundation

- **CI:** a Python 3.11–3.14 matrix (E7), a Windows core job (CE-16), and pytest `--timeout` (E12).
- **Lint and types:** enforce the configured ruff `E,F,I,B,UP` and `ruff format --check`. CI checks only `E9,F63,F7,F82` today, and some F401/F841 debt remains. Extend mypy past its 16 files into `languages/typescript` and `_framework`, ratcheting with per-module ignores. Add import-linter contracts that already hold: `languages` ↛ `app`, `engine` ↛ `app` (E6).
- **Dev tooling:** a `dev` extra with pinned pytest, ruff, mypy, import-linter and pytest-xdist, and make targets that don't `pip install`. Fix the release checklist (E11).
- **Tests:**
  - an autouse isolation fixture plus a guard that fails if the repo's `.desloppify/` is touched (E5);
  - replace `inspect.getsource` and `callable(fn)` tripwire tests with behaviour tests (E9);
  - delete the duplicate `tests/review/integration/*` wrappers (E10);
  - fold `tests/lang/typescript/` into `languages/typescript/tests/` (E13).
- **Grammar preflight:** `desloppify setup --grammars`, and make `is_available()` actually load the tsx grammar (PK-2).
- **Dead code:**
  - `dev test-hermes` (AR-4);
  - `base/optional_deps`;
  - `intelligence/review/dimensions/metadata.py` and `metadata_legacy.py`;
  - the unused `base.registry.register_detector` path, which only plugins used;
  - review the cwd-relative file reads in engine code that the TS-path audit (#6) didn't cover.
- **Declarative detector spec (AR-1, L):** one dataclass covering DetectorMeta, zone policy, the phase and the detect command, registered by the plugin. That turns a new detector from a 6–8-file change into 2 files plus a test.
- **Scope (CE-8):** the plan, triage and review machinery is about 50k lines, much more than the TS plugin's 10k. Freeze it, collapse the state facades, replace the `work_items`/`issues` fallbacks with one accessor, and spend effort on detection accuracy.

### 2F. Fork identity and release

- **Distribution name (PK-4).** The fork can't publish as `desloppify`. Pick a name such as `desloppify-ng`, but keep the `desloppify` import package and console script so migrating is a one-line install change. Then set `PYPI_PUBLISH`, switch publishing to `release: published` / tag `v*`, check that the tag matches the version, and gate it on CI.
- **Upstream pointers.** These still point at `peteromallet/desloppify`:
  - `update_skill/cmd.py` downloads SKILL.md from upstream main; install it from bundled package data instead;
  - `docs/SKILL.md` (clone, issues and `uvx` install);
  - `README.md`;
  - `pyproject.toml [project.urls]`;
  - the release checklist.
- **Attribution.** Keep the upstream LICENSE and copyright. Add a NOTICE/CREDITS section naming upstream and the cherry-picked contributors (awdemos's #744 batch, #617, #760, #629, #750). Keep merging with merge commits so authorship survives.
- **Upstream intake.** New upstream PRs are checked against the fork as #11 did: apply the PR's regression test to fork `main` first, then port only what reproduces. Still to review: the MercurialUroboros TS false-positive commits (UP-3) and #704 (Codex token footer).
- **Community.** Required checks on `main`, Actions enabled for outside contributors, and an announcement on the busiest upstream issues (#501, #615, #705, #665, #715).

---

## 3. Dropped with the TS-only decision

These findings no longer apply, because the code they describe is gone:

- **CE-1:** one `plan.json` shared across language states. There is only one language and one state.
- **CE-13:** exclusions applied after state-path and language resolution, which wrote to the wrong `state-<lang>.json`.
- **CE-14:** auto-detect walking up to an ancestor `package.json`.
- **CE-17:** the JS plugin's ESLint invocation on Windows.
- **GR-8 / roadmap 1.10:** consolidating the regex and tree-sitter graph builders. The TS graph is built on tree-sitter (#3), and the generic builder was deleted (#12).
- **GR-10:** the generic tree-sitter graph's zero edges. Fixed in #1, then deleted in #12.
- **PK-3:** missing elixir/php/r review data in the wheel.
- **E8:** ruby and r tests not collected.
- **AR-3:** the stale plugin guide. `languages/README.md` was rewritten in #14.
- Every Python, Rust, Go, C#, C/C++, Dart and GDScript item, and support for generic plugins.

---

## 4. Appendix: original review findings and status

Status key: **done** (with PR), **partial** (what's left is in §2), **open**, **dropped** (§3). The **gated** status (fixer behind `--unsafe`) is retired: since #24 no fixer is gated. Severities are from the 2026-10-05 review.

### Fixers and move

| ID | Sev | Title | Status |
|---|---|---|---|
| FX-1 | critical | Logs fixer deletes side-effecting declarations and the first line of multi-line declarations | done (#1) |
| FX-2 | high | Line-granular log removal deletes neighbours, changes control flow, breaks JSX and ternaries | done (#23) |
| FX-3 | high | File-wide empty-block removal deletes `.catch(()=>{})` and declarations | done (#23) |
| FX-4 | high | `detect logs --fix` removes the first line only; tag regex matches strings, comments and `${}` | partial (#1 removed the line deletion) → 2.11 |
| FX-5 | high | Import collector swallows the next import; deletes side-effect imports | done (#17) |
| FX-6 | high | `}` or comma in a comment drops bindings; alias handling deletes used names | done (#17) |
| FX-7 | high | unused-vars removes the wrong declaration or declarators | done (#19) |
| FX-8 | high | Destructuring split breaks syntax and changes strings | done (#19) |
| FX-9 | high | `_`-prefix renames destructured props; `c_onst` corruption | done (#20) |
| FX-10 | high | empty-if-chain deletes a non-empty else | done (#24) |
| FX-11 | high | No parse gate before writing; dry-run shows no "after" | done (#16); `--verify` → 2.2 |
| FX-12 | high | move breaks sibling imports, `.js` specifiers, importers outside src | done (#1, #8) |
| FX-13 | medium | Chained `str.replace` double-rewrites and touches strings | done (#1, #8) |
| FX-14 | medium | Write path loses CRLF, symlinks, mode, encoding | done (#1) |
| FX-15 | medium | `_categorize_unused` defaults to "imports" | done (#1, #26) |
| FX-16 | low | Autofix resolves the wrong issue IDs | done (#18) |
| FX-17 | low | dead-useeffect deletes the preceding `//` line | done (#32) |
| FX-18 | low | BOM hides the line-1 import (fails safe) | done (#1 strips the BOM before fixing; #30 adds a round-trip case) |
| FX-19 | medium | No adversarial or round-trip fixer tests | done (#30) |

### External tools

| ID | Sev | Title | Status |
|---|---|---|---|
| TL-1 | high | tsc: bogus `npx tsc`, hard-coded tsconfig.app.json, unchecked rc, temp file in repo, dropped codes | done (#1) |
| TL-2 | high | Knip runs in the wrong dir, uses `pos` as line, silent when missing; drops categories | done (#1); categories → 3.4 |
| TL-3 | high | tsc type errors never reported | open → 3.1 |
| TL-4 | high | No ESLint/Biome/oxlint | open → 3.2 |
| TL-5 | high | `next lint` removed in Next 16 | done (#1, upstream #760) |

### Graph, resolver and entry points

| ID | Sev | Title | Status |
|---|---|---|---|
| GR-1 | high | tsconfig: JSONC, references, extends, baseUrl, `@/` fallback | done (#1, #5, #8, #37); npm `#x` targets → 2.34 |
| GR-2 | high | Workspaces and package exports not modelled | done (#5) |
| GR-3 | high | Missing require / import=require / triple-slash; `.mts`/`.cts` ignored | done (#3, #10) |
| GR-4 | medium | Comment and string imports create edges; `import type` creates false cycles | done (#3) |
| GR-5 | medium | Files with no imports never enter the graph | done (#1) |
| GR-6 | medium | No package.json or framework entries; loose dynamic match; Next 16 `proxy.ts` | done (#1, #3, #5, #39); follow-ups → 2.35, 2.36 |
| GR-8 | medium | Separate regex and tree-sitter graphs | dropped |
| GR-9 | medium | Results depend on the cwd | done (#6) |
| GR-10 | critical | Shared tree-sitter graph had zero edges | dropped (fixed in #1, deleted in #12) |

### Detectors

| ID | Sev | Title | Status |
|---|---|---|---|
| DT-1 | high | Global `matches[:50]` drops most smells | done (#1) |
| DT-2 | high | Body extractor grabs param or return-type braces | done (#1, upstream #629; #11 added tests) |
| DT-3 | low | Function extractor misses async/default/methods | done (#47) |
| DT-4 | medium | Props detector counts lines, skips extends/generics/intersections | open → 2.8 |
| DT-5 | medium | Deprecated detector false positives; "safe to delete" on public API | partial (#1) → 2.10 |
| DT-6 | low | Facade misses multi-line, `export * as`, `'use client'` | done (#5, #22, #36) |
| DT-7 | medium | eval/innerHTML false positives; comments not stripped | partial (#1, #11) → 2.11 |
| DT-9 | medium | Non-null, block `@ts-ignore`, double-cast gaps | done (#54) |
| DT-10 | medium | Author-specific heuristics | open → 3.5 |
| DT-11 | medium | test-d, bench, e2e, config, generated not zoned | partial (#1) → 2.12 |
| DT-12 | high | Jest-only assertions; inverted test-health; cross-package basename mapping | partial (#1, #8, #45) → 2.17, 3.11 |
| DT-13 | medium | tsconfig strictness never read | open → 3.3 |
| DT-14 | high | No framework support beyond Next.js (React Router entries only); SFCs unanalysed | open → 3.6, 3.7 |
| DT-15 | medium | No server-action auth, raw-SQL or child_process checks | open → 3.8 |
| DT-16 | low | Review prompts are Python-flavoured | open → 3.10 |
| DT-17 | low | God rules are React-only | open → 3.9 |

### Core engine, CLI and config

| ID | Sev | Title | Status |
|---|---|---|---|
| CE-1 | high | One plan.json across languages | dropped |
| CE-2 | high | Strict never recovers from real fixes | done (#38) |
| CE-3 | medium | One bad issue loses the whole state | done (#25, #29); plan sections → 2.30 |
| CE-4 | medium | Unlocked read-modify-write | done (#35) |
| CE-5 | medium | Deferred, triaged_out and wontfix never auto-resolve | done (#42) |
| CE-6 | medium | scoring.md and QUEUE_LIFECYCLE contradict code | done (#44) |
| CE-7 | low | Subjective scores taken as-is | open |
| CE-8 | low | Plan subsystem complexity | open → §2E |
| CE-9 | low | Carried-forward dimensions never expire | open → 2.24 |
| CE-10 | low | Concerns count suppressed issues | open → 2.24 |
| CE-11 | low | Recompute is O(dims × issues × modes) | open |
| CE-12 | medium | Headline score from zeroed subjective dims; ci profile; cycles under Security | open → 2.23 |
| CE-13 | medium | Exclusions applied after language and state resolution | dropped |
| CE-14 | medium | Auto-detect walks to an ancestor package.json | dropped |
| CE-15 | low | No detector or domain disable | open → 3.12 |
| CE-16 | medium | Codex runner Popen is locale-dependent on Windows | open → §2E |
| CE-17 | medium | JS ESLint on Windows | dropped |

### Tests, CI, packaging, architecture, upstream

| ID | Sev | Title | Status |
|---|---|---|---|
| E1 | high | No TS fixtures or e2e scan | done (#2) |
| E2 | high | tsc and knip always mocked; no Node in CI | done (#2) |
| E3 | high | CI red on main; PyPI publish ungated | done (#1, #4); name → §2F |
| E4 | medium | Glob-order test fails on tmpfs | done (#1, upstream #617) |
| E5 | medium | Tests write into the repo's `.desloppify` | open → §2E |
| E6 | low | Lint 4 codes, mypy 16 files, 1 import contract | open → §2E |
| E7 | low | CI only on py3.11 | open → §2E |
| E8 | medium | Ruby and R tests never collected | dropped |
| E9 | medium | getsource and callable tripwire tests | open → §2E |
| E10 | low | Review tests run twice | open → §2E |
| E11 | low | Make targets reinstall; release checklist drift | open → §2E |
| E12 | low | No test timeout | open → §2E |
| E13 | low | TS tests in two trees | open → §2E |
| PK-1 | medium | tree-sitter floor crashes; cap blocks working releases | done (#1) |
| PK-2 | medium | Offline grammar download silently drops findings | partial (#1 reports reduced coverage) → §2E |
| PK-3 | medium | Wheel omits elixir/php/r review data | dropped |
| PK-4 | medium | Fork inherits PyPI name and upstream URLs | partial (#4 gated publish) → §2F |
| AR-1 | medium | New TS detector touches 6–8 files | open → §2E |
| AR-2 | medium | Regex-based TS plugin | partial (#3 imports on tree-sitter, #40 shared helper) → 2.33 |
| AR-3 | medium | Plugin guide describes nonexistent files | done (#14) |
| AR-4 | low | Dead compat shims, `dev test-hermes` | partial (#12 removed the shims) → §2E |
| UP-1 | high | Upstream abandoned | — |
| UP-2 | high | #744 + #617 + #760 + #629 merge clean | done (#1) |
| UP-3 | medium | MercurialUroboros TS false-positive commits | open → §2F |
