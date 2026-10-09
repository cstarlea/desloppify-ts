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
| #44 | `docs/scoring.md` and `dev/QUEUE_LIFECYCLE.md` rewritten from the code; roadmap catch-up (2.22) |
| #45 | Test coverage follows imported names through re-export chains of any depth (2.16) |
| #46 | `plan skip --permanent`/`--false-positive` change deferred and triaged_out issues too; `--deferred-only` (2.38) |
| #47 | Function extractor and the function-shape smells on `syntax.queries.definitions()` (2.6) |
| #48 | A module reached from a tested public entry counts as covered; separate file and √LOC-weight labels (2.17) |
| #49 | `fixed`/`false_positive` are marked `scan_verified` only on a confirmed absence (2.39) |
| #50 | Cluster completion counts every resolved status, in scan reconcile and `plan resolve` (2.40) |
| #51 | Name-aware re-export following replaces the one-hop barrel and facade expansions in test coverage (2.16) |
| #52 | Reconcile keeps the skip entries of wontfix and false_positive issues (2.41) |
| #53 | A scan never changes a `false_positive` status (2.42) |
| #54 | Type-safety smells as syntax-tree queries (2.7) |
| #55 | Cluster members removed by resolve or supersede stay removed (2.43) |
| #56 | `has_testable_logic` on the syntax tree: types-only files are no longer scored by Test health (2.44) |
| #57 | Comment-, string-, template- and regex-aware `code_text` and `scan_code` for the smells and their brace matching (2.11, partial) |
| #58 | Wontfix debt totals in stats, `status` and the narrative leave out wontfix issues a scan confirmed gone (2.37) |
| #59 | Plan quarantine checks the entries of `superseded`, `execution_log`, `commit_log`, `promoted_ids` and `uncommitted_issues` (2.30) |
| #60 | `definitions()` also returns object-literal methods and anonymous default exports (2.6) |
| #61 | Props detector on the syntax tree (2.8) |
| #62 | Carried-forward dimensions expire after 3 scans; concerns skip suppressed and out-of-scope issues (2.24) |
| #63 | Headline the objective score, marked provisional, until a subjective dimension is assessed (2.23) |
| #64 | `scan --profile ci` prints a plain report; `--fail-under`/`--fail-score` exit code (2.23) |
| #65 | Deprecated detector on the syntax tree (2.10) |
| #66 | `cycles` scores under Code quality instead of Security (2.23) |
| #68 | Deprecated importers followed through object-literal namespace aliases (2.10) |

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
| 2.33 | **Done.** Cohesion and `deps/imports` parse through `syntax.tree.parsed_file` (resolved path, grammar by extension): `TreeSitterLangSpec` takes a `parse_file` hook instead of a grammar, and cohesion compiles its function query per grammar. Every file is now parsed once per scan: trpc 2845 → 1038 parses, zod 1527 → 524, ky 261 → 87, commerce 152 → 66; issues identical | S | AR-2 |

### 2B. Detector accuracy (rest of Milestone 2, plus M0 and M1 leftovers)

| # | Item | Effort | Findings |
|---|---|---|---|
| 2.6 | **Done (#47).** The function extractor and the five function-shape smells read `syntax.queries.definitions()`: declarations and named function expressions, variable-bound or assigned functions, and class members (`Owner.member`), so async, default exports, methods, multi-line/destructured params and concise arrows are seen and bodies come from the tree. `detector_flow` builds the list once per file; `async_no_await` looks for `await` in the function's own body. Signature variance skips default exports and uppercase route-handler names. Regex stays only as the fallback without tree-sitter. #60 adds object-literal methods and function-valued pairs (named by their path, e.g. `api.get`, `default.fetch`) and anonymous `export default` functions (`default`) | M | DT-3 |
| 2.7 | **Done (#54).** Type-safety smells read the syntax tree (`detector_types`): every non-null `!` (not just `x!.y`), `as unknown as T` / `<T><unknown>x` (`double_cast`), `any` in any type position (`keyof any` left out; `as any` / `<any>x` stay `as_any_cast`), and `@ts-ignore` / `@ts-expect-error` by TypeScript's own directive rules, block comments included. An `@ts-expect-error` with no reason after it or in the comment above is its own low-severity smell (`ts_expect_error_undocumented`), outside test files; explained ones aren't reported. `x[i]!` isn't reported when the nearest tsconfig enables `noUncheckedIndexedAccess`. Each per-file issue carries its density (matches per 1,000 lines). Regex stays only as the fallback without tree-sitter | M | DT-9 |
| 2.8 | **Done (#61).** Props detector on the syntax tree (`syntax.queries.type_declarations`): an interface or type alias counts its distinct property and method names, including those inherited through `extends`, intersections, generic declarations and `Partial`/`Required`/`Readonly`/`Omit`/`Pick` of types declared in the scan (imports resolved); package types add nothing, a union counts its largest variant, same-name interfaces merge. Props/Context/State must be whole words of the name (`UIState`, bare `Props`; not `Statement`). A plain rename of a checked type and types without properties are skipped. Regex line counting stays only without tree-sitter. trpc `TRPCContextState` (24) is newly reported; zod `ToJSONSchemaContext` 17 → 15 | M | DT-4 |
| 2.9 | **Done (#22).** Facade: every top-level statement is a re-export (directives and comments allowed); cover multi-line, `export * as`, `export type *` | S | DT-6 |
| 2.10 | **Done (#65).** Deprecated detector on the syntax tree: a `/** @deprecated */` comment attaches to the next sibling node, as in TypeScript (declarations, every declarator, class/interface/object/enum members, and `export { a as b }` specifiers, so deprecated barrel aliases are found; `$` names, `async function` and long JSDoc no longer slip past). Importers are files whose imports reach the export through the import graph (re-export chains, `export *`, type-only imports, namespace members like `z.cuid()`, also through `const z = { ...ns, iso: _iso }` objects), not files that mention the name; same-file uses are identifier references. A deprecated overload whose sibling signatures aren't all deprecated is an `overload`, not an issue. `.d.ts` files are skipped. Each file is parsed once (the detector owns the parse cache outside a scan); the regex fallback reads each file once and greps all names in one pass. zod and trpc importer counts drop from name collisions (`ZodErrorMap` 77 → 1, `inferProcedureInput` 23 → 0); 31 new deprecated symbols. #68 follows importers through object-literal namespace aliases (`const z = { ..._schemas }`) | M | DT-5 |
| 2.11 | **Done: every TS line-regex detector, and the engine-level security scan, ignores comments, strings, template and JSX text.** #33 put `dead_useeffect` on the syntax tree and #57 added the `code_text` lexer. #69 adds `SourceText`: each line, with every offset classed as code, comment, string, template or regex literal. A pattern counts only where its match starts in code. `todo_fixme`, `workaround_tag` and `ts_nocheck` must match inside a comment, and `hardcoded_url` and `debug_tag` must open a string. Structural smells are on the syntax tree, with that masked regex as the fallback: `empty_catch` (now `catch {}` too), `swallowed_error` (multi-line `console.*` calls), `catch_return_default`, `switch_no_default` (nested parens, bodies over 5000 chars), `sort_no_comparator`, `voided_symbol` and `empty_if_chain` (`if (a) {}` followed by a non-empty `else` is no longer reported). The logs detector uses the debug-logs fixer's tagged-call query, so it finds multi-line calls on the row the fixer looks up. The rest run on the masked text: TS security line and file checks (`JSON.parse` try scope counts code braces only), `console_error_no_throw`, `window_global`, the colour, rgb, magic-number and URL smells, the complexity signals (nested ternaries, TODOs, imports, inline types), mixed concerns, the React context, state-sync and hook detectors, and the pattern census. The engine-level security scan (`engine/detectors/security`) takes each TS file's lines from `SourceText` through a `security` language hook, instead of skipping lines that start as comments: secret formats match anywhere, a comment or string included; a `hardcoded_secret_name` name must be code; `insecure_random` needs `Math.random(` in code, with its context word anywhere but a comment; TLS settings must start in code; `log_sensitive` needs the log call in code and the sensitive word in code, or in a string when the call also logs a value (`console.log("Authorization:", header)` yes, `console.error("Invalid token")` no). The lexer takes JSX text spans from the syntax tree (`jsx_text_spans`, kind `jsx`) in `.tsx`/`.jsx`/`.js` files, so an apostrophe in JSX text no longer hides the rest of the line; every TS detector that has the file's path passes it | M | DT-7, FX-4 |
| 2.12 | **Done.** Zones: a leading `@generated` / "auto-generated" / "Code generated … DO NOT EDIT" comment puts a file in the generated zone (override and vendor paths win); directory-level issues (`flat_dirs`, `naming`) take the directory's zone from its directory patterns, else its files' common zone. The generated and vendor zones now also skip `responsibility_cohesion` and `signature`, which leaked generated-file findings. `examples/` and `example/` directories are now in the script zone, and the script zone skips `test_coverage` (Test health scores production files only). `www/` stays production; a directory override (`zone set www <zone>`, #80) covers it | S | DT-11 |
| 2.13 | **Done (#26).** A separate `params` category for unused symbols, with every category decided on the syntax tree | S | FX-15 |
| 2.14 | **Done (#37).** package.json `imports` (`#subpath`) in the resolver: the importer's nearest package.json is the scope; exact and `*` keys, condition objects and fallback arrays in order; bare targets resolve when they name a workspace package. The vite-react golden's `analytics.ts` false positive is gone | S | GR-1 |
| 2.15 | **Done (#39).** The `_NEXTJS_*` constants left `engine/detectors/orphaned.py` for `NEXTJS_SPEC.entry_conventions`; the language passes the specs' conventions to the detector through `OrphanedDetectionOptions`, since detectors may not import the language layer. Behaviour unchanged (commerce issue IDs identical) | S | GR-6 |
| 2.16 | **Done (#45).** Test coverage follows each imported name through re-export chains of any depth (`export { } from`, `export *`, `export * as ns`, import-then-export) to the file that defines it, via `syntax.queries`; namespace imports follow the members the test uses; type-only names aren't followed. trpc `parseTRPCMessage.ts` is directly tested. With tree-sitter, name-aware following replaces the one-hop name-blind barrel and facade expansions, which stay as the fallback (#51); anonymous `export default` counts as a definition | M | DT-12 |
| 2.17 | **Done (#48).** A module reached through the import graph from a directly tested public entry (package.json `exports`/`main`/`types`...) counts as covered, not `transitive_only`; reached only from a tested internal module, it stays transitive. The scan line reports scored files and the √LOC weight separately; `status` and the plan table mark Test health's Checks with `*` (√LOC-weighted), and `next` reports weighted failures instead of "of N checks". trpc test health 36.6% → 42.3%, zod 74.0% → 74.5% (on top of #45). Files under 10 lines are documented as unscored | M | DT-12 |
| 2.28 | **Done (#36).** A file whose directive prologue holds `'use client'` or `'use server'` (after `'use strict'` or comments too) is not a facade, even if it only re-exports: in Next.js it marks a client or server boundary. None appear in the four repos | S | DT-6 |
| 2.29 | **Done (#71).** One line rule for every detector and fixer: tsc's. LF, CR, CRLF, U+2028 and U+2029 end a line, and VT, FF and U+0085 don't. `syntax/lines.py` holds the rule. `SourceText` and the text-based detectors split with it: smells, logs fallback, security, React, patterns, components, and the unused fallback and categoriser. `ParsedSource.line`/`end_line`/`line_text` convert tree-sitter rows, which count LF only, to tsc lines. A fast path keeps rows when the file has no lone CR or U+2028/U+2029. Query spans, the tree smells and the debug-logs, empty-if-chain, dead-useeffect and unused-imports fixers use these, and so does `NameIndex`'s fallback match. Reported lines therefore match what tsc reports and what each fixer looks up. The round-trip corpus has a file with each of these characters in strings, comments and a template, with findings after them that must all be fixed in one run. Not converted: the deprecated and props detectors (other work in progress) and line counts used only as LOC | S | — |
| 2.34 | **Done.** A `#x` import that the importer's package.json maps to an npm package (any condition or fallback target that is a declared dependency, a Node builtin or `node:x`; `*` targets like `undici/*` too) counts as external like a bare import, so it no longer lowers orphan confidence. A target nothing declares stays unresolved, as a bare import does | S | GR-1 |
| 2.35 | **Done.** `detect orphaned` reports exactly what `scan` does: both go through `phases_coupling.find_orphans`, so the command now applies zones (config, test, generated…), package.json entry files and the unresolved-import confidence; `--json` includes the confidence. On origin/main it disagreed on every review repo (trpc 105 vs 27, zod 130 vs 20, ky 42 vs 0, commerce 1 vs 0); now identical. "1 files"/"1 issues" in the tree and orphaned output are singular (`terminal.plural`) | S | GR-6 |
| 2.36 | **Done.** One `next.config.{js,mjs,cjs,ts}` list serves both framework detection and the entry conventions, so App Router files in a package with `next.config.cjs` are entries | S | GR-6 |
| 2.44 | **Done (#56).** `has_testable_logic` decides on the syntax tree which files Test health scores: a file holding only comments, directives, imports, type aliases, interfaces, ambient declarations and re-exports is not testable logic. The line heuristic ended a type context after the first line of a multi-line type alias or conditional type, so types-only files were scored (zod 169 → 165 scored files, trpc 451 → 427); it stays as the fallback without tree-sitter | S | DT-12 |

### 2C. Engine and state correctness

| # | Item | Effort | Findings |
|---|---|---|---|
| 2.18 | **Done (#38).** Strict no longer counts scan-confirmed resolutions (`auto_resolved`) as failures; verified counts a manual `fixed`/`false_positive` once a rescan confirms it (`scan_verified`); a mechanical dimension whose detector ran with zero checks left is no longer carried forward with its old score. A test checks that fixing and rescanning reaches 100 in every mode; scoring.md and SKILL.md describe the three modes as the code does | S | CE-2 |
| 2.19 | **Done for `state.json` (#25) and `plan.json` (#29).** Resilient loading: quarantine invalid issues and plan entries instead of discarding the whole file; rename the bad file to `.corrupted`; don't rotate `.bak` after a failed load; coerce the version field | S | CE-3 |
| 2.20 | **Done (#35).** Every mutating command holds the state lock, then the plan lock, from its first load to its return; read-only commands load unlocked, and the corrupt-file rename and `.bak` restore run under the lock (or in memory if it stays busy). One re-entrant, ranked file lock backs `state_lock`, `plan_lock` and the progression log, whose trim now runs under the append lock. `plan triage --run-stages` and `review --run-batches`/`--scan-after-import` stay unlocked because they wait on desloppify subprocesses | M | CE-4 |
| 2.21 | **Done (#42).** Deferred and triaged_out issues auto-resolve when a scan confirms they're gone, under the same conditions as open issues, and reconcile supersedes their skip entries. Wontfix stays wontfix: the scan marks it `scan_verified`, so it stops failing strict and verified, and clears the mark if the finding returns; its skip entry is kept. A superseded entry is dropped once its issue reappears so a fresh skip or queue entry isn't stripped | S | CE-5 |
| 2.22 | **Done (#44).** `docs/scoring.md` (and its bundled copy) and `dev/QUEUE_LIFECYCLE.md` rewritten from the code, citing the functions behind each rule; the lifecycle doc lists the gaps found as 2.38–2.40 | S | CE-6 |
| 2.23 | **Done (#63, #64, #66).** Until a subjective dimension is assessed, the scan summary, `status`, the LLM block, `query.json` (`headline`) and the scorecard lead with the objective score marked provisional (`headline_score`); stored scores are unchanged (#63). `scan --profile ci` prints a plain report with no colour, coaching or agent blocks, and `--fail-under SCORE` (`--fail-score`, default objective) exits 1 below the threshold (#64). `cycles` scores under Code quality instead of Security (#66) | M | CE-12 |
| 2.24 | **Done (#62).** A carried-forward dimension (mechanical; subjective ones are never carried) expires after `CARRIED_FORWARD_MAX_SCANS` (3) scans without its detectors running, counted from `carried_forward_since_scan`. Concerns use only the issues the score counts (not suppressed, inside the scan's `--path`); dismissal cleanup still sees every open issue | S | CE-9, CE-10 |
| 2.25 | **Done (#21).** Commands with `--path` (autofix, detect, …) default to the last scan's path, as `review` already does, and fall back to `src/` only without one. A bare `scan` re-scans the last scope too | S | — |
| 2.30 | **Done (#59).** Plan quarantine coverage: the entries of `superseded`, `execution_log`, `commit_log`, `promoted_ids` and `uncommitted_issues` are checked against the fields their readers use (record shape, string `timestamp`/`action`/`sha`, ID-string lists) and bad ones are quarantined like #29's sections; before, one bad entry crashed `plan`, `plan commit-log history` or `scan` | S | CE-3 |
| 2.31 | **Done (#41).** `tree` and `viz` label the root node with the scanned path relative to the project root (the project's directory name for a whole-project scan) and strip only that prefix, so `--path .` no longer merges `src/` into the root; `--focus` takes scan- or project-relative paths | S | — |
| 2.32 | **Done (#43).** `cli.main()` configures logging once: a stderr handler on the `desloppify` logger prints `  WARNING: message`, yellow (red for errors) on a terminal unless `NO_COLOR` is set. No global verbosity flag exists, so `DESLOPPIFY_LOG_LEVEL` (e.g. `DEBUG`) sets the threshold | S | — |
| 2.37 | **Done (#58).** Wontfix debt totals follow strict: a wontfix issue a scan confirmed gone (`scan_verified`) is no longer debt. `is_wontfix_debt()` sits beside `issue_counts_as_failure()`; `stats.wontfix_debt`/`wontfix_debt_by_tier` feed the scan gap warning, Score Integrity, the agent summary, `status` structural areas and tier table, and the narrative; `stats.wontfix` stays the status count | S | CE-5 |
| 2.38 | **Done (#46).** `plan skip` now changes `open`, `deferred` and `triaged_out` issues, so `--permanent`/`--false-positive` on a deferred issue makes it `wontfix`/`false_positive` in state as well as plan; wontfix, false_positive and resolved issues are left alone in both. The deferred-disposition item suggested `plan skip --permanent "*"`, which wontfixed every open issue and no deferred one; it now suggests the new `--deferred-only` flag | S | CE-5 |
| 2.39 | **Done (#49).** `fixed`/`false_positive` are marked `scan_verified` only on a confirmed absence (detector ran, zone policy now skips it, or file gone), like the other statuses; issues outside `--path` keep their mark as it was. An already-marked issue is no longer re-marked every scan, and the user's note is kept | S | CE-2 |
| 2.40 | **Done (#50).** Cluster completion counts every resolved status (`resolved_statuses()`: fixed, wontfix, false_positive, auto_resolved) in scan reconcile and in `plan resolve`. The resolve path was the visible bug: members recovered from the execution log kept a cluster resolved one issue at a time from ever closing ("1 left in cluster") | S | — |
| 2.41 | **Done (#52).** Post-scan reconcile superseded every non-actionable queue/cluster reference, popping the `plan.skipped` entry of a clustered or queued `wontfix`/`false_positive` issue. The decision was lost from the plan, and a false positive whose finding was still present reopened as `open` at the following scan. A decided issue is now only detached from the queue, promoted list and clusters; its skip entry stays and it is not superseded (found while fixing 2.40) | S | CE-5 |
| 2.42 | **Done (#53).** A `false_positive` issue whose finding was still reported was reopened by every scan (note "Reopened (×N)", `reopen_count` + 1, attestation dropped), then set back to `false_positive` by reconcile from its skip entry; without a skip entry it stayed `open`. It now follows the wontfix rule: a scan never changes a `false_positive` status, and a returning finding only clears `scan_verified` | S | CE-5 |
| 2.43 | **Done (#55).** `normalize_cluster_defaults` replayed the execution log's `cluster_add` entries into cluster `issue_ids` on every load, so members removed by `purge_ids` (resolve) or by supersede came back. The replay now runs only when upgrading a legacy plan; writers update `issue_ids` directly | S | — |

### 2D. New capabilities (Milestone 3)

| # | Item | Effort | Findings |
|---|---|---|---|
| 3.1 | **Done (#75, #76).** `detectors/tsc.py` runs tsc once per scan (cached in the runtime cache) and both `unused` and the new `type_error` detector read it; forcing `noUnusedLocals`/`noUnusedParameters` adds only the TS6133 family, so the other diagnostics are identical (checked on ky, commerce, zod, trpc) (#75). `type_error` reports one issue per code and line (`type_error::<file>::TS####::<line>`) under a new mechanical **Type checks** dimension, file-based over the files tsc checked (`--listFiles`). Only files owned by the tsconfig that ran are reported: a monorepo root config isn't the config a package is checked with (trpc's root reports 146 errors in one example that its own config reports 35 for), so other tsconfigs' files are left out and named in reduced coverage. Config errors (TS5xxx/TS6xxx) aren't issues; a bare-specifier `Cannot find module`, missing `@types` or JSX types marks its file as environment noise (not reported, not counted); with dependencies declared and no `node_modules`, the detector skips. Strictness- and config-sensitive codes (implicit any, TS4111, TS2686, TS1479, TS2304, TS2578, ...) are medium confidence. No potential when tsc doesn't run, so the dimension carries forward and old issues aren't auto-resolved (#76) | M | TL-3 |
| 3.2 | **Done (#84 ESLint, #87 XO/Biome/oxlint).** The new `lint` detector runs every linter configured in the nearest directory (`eslint.config.*`, XO, `biome.json(c)` with its linter on, `.oxlintrc.json`; legacy ESLint configs only when nothing else is above) with its local `node_modules/.bin` binary and JSON output; oxlint codes are renamed to ESLint's so a shared rule is one issue; one issue per rule and line (`lint::<file>::<rule>::<line>`) under a new mechanical **Lint** dimension, file-based. Nested flat configs are other projects (left out, named in reduced coverage). Formatting rules and rules that duplicate desloppify's detectors (unused vars, `no-explicit-any`, `ban-ts-comment`, ...) are dropped; confidence from `meta.type`, a table for the type-aware typescript-eslint rules (async bugs high, `no-unsafe-*` medium, cleanups low) and conventions (low); `warn` lowers a step. Missing ESLint or deps, a broken config, OOM or timeout skip with no potential. A type-aware config over `lint_type_aware_max_files` (400) files is skipped (trpc `packages/`, 637 files, ran out of heap). Replaces the Next.js `next_lint` tool phase | M | TL-4 |
| 3.3 | **Done: `tsconfig_health`.** The projects are the nearest tsconfigs (`find_nearest_tsconfig`) of scanned TS files outside test, generated and vendor zones. Options are read through `extends` and JSONC (`traced_compiler_option`, which also says when an uninstalled base leaves an option unknown; unknown is never reported). Well-known bases count when they are not installed: `@tsconfig/*`, `@tsconfig/strictest`, `@total-typescript/tsconfig`, `@sindresorhus/tsconfig` and `@vue/tsconfig`. `strict` off is tier 3: high confidence when it is never set, medium for `"strict": false`. It isn't reported on TypeScript 6+, where strict is the default (the version comes from `node_modules`, then `package.json`, including pnpm catalogs), or when `noImplicitAny` and `strictNullChecks` are both on. With strict on, a never-set `noUncheckedIndexedAccess` (tier 3), `noImplicitOverride` or `verbatimModuleSyntax` (tier 2, medium confidence) is reported. A value set anywhere, `false` included, is a decision and isn't reported. `noImplicitOverride` only applies where a class extends another; `verbatimModuleSyntax` only where the output is ES modules (not CommonJS `module`, and not a `node16`/`nodenext` package without `"type": "module"`). In a repo with several projects, only the root project and published packages get these checks; examples, docs sites and benchmarks get the strict check alone. An unset option is reported once, on the outermost repo config the project extends (a monorepo's shared base); a value that is set is reported where it is set. `drift` (tier 2) compares published packages and names the options most of them turn on that one leaves off. IDs are `tsconfig_health::<tsconfig>::<option>` or `::drift`. It scores under Code quality, not Type checks, so that a scan where tsc can't run still carries Type checks forward. Its issues count in the config zone. The holistic review context gets the open issues as `abstractions.type_strictness` | S | DT-13 |
| 3.4 | **Done.** Knip runs once per scan (runtime cache) and its JSON is found after lines plugins print to stdout. `exports` adds unused enum members (`exports::<file>::<Enum>.<Member>`) and duplicate exports (`exports::<file>::<a>=<b>`, tier 3, medium; a `@deprecated` alias or one already unused doesn't count). `orphaned` uses Knip's unused files as corroboration only: agreement raises an orphan to high confidence (unless Knip can't resolve an import that may name it either), a file Knip reaches drops to low. A new `dependencies` detector (Code quality) reports `unused`/`unused_dev`, `unlisted` (one per manifest and package) and script `unlisted_binary`, as `dependencies::<package.json>::<kind>::<package>`; a manifest with uninstalled dependencies skips unused and binaries (reduced coverage), and a dependency still imported, named in a script or configured under its own manifest key isn't unused. Unresolved imports aren't reported (tsc's TS2307 covers TS files; Knip calls an unknown bare package unlisted); namespace members, catalog and cycles aren't read. `exports` no longer reports a published package's public API: names its manifest entry points expose, followed through re-exports (ky 39 → 8, trpc 156 → 63). The import graph follows MDX ESM imports (not code fences) and Docusaurus's `@site/` alias, so components MDX docs import aren't orphans (zod 8, trpc 3 fewer) | M | TL-2 |
| 3.5 | Replace the original author's project-specific heuristics with config and presets: pattern families, boundary rules, data-layer identifiers, a `supabase` FrameworkSpec, public-env-secret prefixes (`NEXT_PUBLIC_`, `VITE_`, `PUBLIC_`, `EXPO_PUBLIC_`) | M | DT-10 |
| 3.6 | **Done for React Router (#96), NestJS (#100), Express/Hono/Fastify (#102), Angular (#104); SvelteKit/Nuxt/Astro scanners open (their detection and entries came with 3.7).** Each spec has detection and entries, and `EntryConventions` gains `marker_dependencies` (a package marked by a dependency alone, scope prefixes allowed), `root_depth`, `entry_dir_names` and `declared_entries` (entries a package names in its own files). The orphaned detector no longer hard-codes React Router. Entries: React Router's route modules from `app/routes.ts` (honouring `appDirectory`), `routes.ts` and the RSC entries; `nest-cli.json` entry files; `@fastify/autoload` directories; HonoX routes/islands/client/server; the wrangler `main`; the files `angular.json` or an Nx `project.json` names (main, polyfills, server, test, karma/protractor configs, file replacements) and `test-setup.ts`. Scanners: `react_router` (route config naming a missing module, Remix imports in v7, `useLoaderData`/`useActionData` without a loader/action), `nestjs` (unregistered controller, provider with dependencies but no `@Injectable`), `express` (Express 4 async handler awaiting without catch, three-parameter error middleware), `hono` (response built but not returned, `next()` not awaited), `fastify` (async plugin/hook taking `done`), `angular` (missing `templateUrl`/`styleUrl(s)`, standalone flag against `declarations`/`imports`). False positives: `single_use` skips classes a DI container wires (a spec's `injected_class_decorators`: Nest and Angular); test coverage skips shape-only classes (DTOs, entities, empty decorated modules; a function in a decorator is logic); a script's `--opt=path` is an entry. #90's god-class rule was checked on immich's NestJS server and kept. Real code: nestjs-boilerplate 178→138, immich server 643→617, fastify/demo 65→49 (all 16 orphans), honox-examples −13 orphans, ngx-admin 156→151, angular-spotify 406→355, epic-stack and React Router templates/playground −7 orphans; 0 added; ky/zod/trpc/commerce unchanged | XL | DT-14 |
| 3.7 | **Done (#97 conventions, #98 extraction).** Detectors read a `.vue`/`.svelte`/`.astro` file through its *code view* (`base/discovery/sfc.py`): the text with everything outside its script blocks blanked and line breaks kept, so lines and columns are the real file's and issue IDs, `show`, fixers and review packets need no mapping. A scanner finds Vue's top-level `<script>`/`<script setup>` (template, style and custom blocks skipped whole), Svelte's instance and module scripts (not in `<svelte:head>`) and Astro's frontmatter and `<script>`s; comments are skipped, attributes may come in any order, `lang`/`type` picks TS, JS or not code, `<script src>` is a graph edge. The file list (`file_finder`) and the parse cache include components, so smells, security, structural, signature, dupes, logs, cohesion and test coverage (script lines as LOC; a template-only component has no logic) analyse them; the import graph seeds them as modules and orphan candidates. tsc doesn't read components: `type_error`/`unused` say so in reduced coverage, and a shim-less `import './X.vue'` (TS2307) isn't an error; a tsconfig extending a missing generated file (`.svelte-kit`, `.nuxt`) skips type errors. Fixers edit the view; the edit is carried back only when every changed span is inside one block and the result reads back as the edited view (else the file is skipped), round-trip corpus extended. Entry conventions and specs (detection only) for Nuxt, Vue + Vite plugins, SvelteKit and Astro, `unplugin-*` generated registries, and the Nuxt/SvelteKit aliases (#97). Golden `vue-vite` and `sveltekit-app` fixtures. ESLint isn't run on components | M | DT-14 |
| 3.8 | **Done.** Four `security` rules on the syntax tree (`detectors/security/backend.py`), with IDs that name the function rather than the line. `sql_injection` (tier 2, critical): a raw-SQL sink called with an interpolated template or concatenation, following a local variable to its string; `$queryRawUnsafe`, `sql.raw`, `Prisma.raw`, `knex.raw`/`*Raw`, `sql.unsafe` count always (high), generic `query`/`execute`/`prepare`/`raw` (high) and `exec`/`run`/`all`/`get` (medium) only when the literal text reads as a statement. Tagged templates, constants (`ALL_CAPS`, const literals, numeric parameters, `Number()`, `.length`) and placeholder lists aren't values. `shell_injection` (tier 2, critical, high): `exec`/`execSync` from `child_process` (imported, required, via the module, through `promisify`, or one hop through a local module exporting such a wrapper) with an interpolated command; `spawn`/`execFile` only with `shell: true`. `server_action_missing_auth` / `route_handler_missing_auth` (tier 3, high severity, medium confidence, low when `middleware.ts`/`proxy.ts` checks auth): exported `'use server'` functions and mutating App Router `route.ts` handlers that call no auth/session function (known names, auth-shaped names, same-file helpers, wrappers; imported wrappers and procedure builders pass) and compare no shared secret. Only apps whose nearest `package.json` depends on an auth library are checked (or when `languages.typescript.auth_functions` is set, which also adds names); a handler that passes its request on is assumed to delegate the check. The security phase now passes language settings to `detect_lang_security_detailed` and salts its cache with them. Real code: 0 new issues on ky, zod and commerce, +2 on trpc (`@trpc/upgrade` `pkgmgr.ts` interpolating a package name into `promisify(exec)`), both correct; commerce's 5 guest-cart actions and revalidate route are left alone by the accounts gate | M | DT-15 |
| 3.9 | **Done.** `structural` flags god classes from the syntax tree (`classes()`): a class meeting two of 20+ methods (arrow-function fields count), 7+ constructor dependencies (parameters plus Angular `inject()` fields), 40+ decorators on the class, methods and parameters (field decorators don't count) and 300+ lines adds one `god class(es)` signal to its file's `structural::<file>` issue, with the classes in `detail.god_classes`; `detect gods` lists classes beside components. One rule alone never flags, so DI-heavy NestJS services and decorated controllers pass. On ky, zod, trpc and commerce, no new issue: Ky (35 methods, 1145 lines) and zod v3's ZodType/ZodString join files already flagged as large | M | DT-17 |
| 3.10 | **Done.** `dimensions.override.json` replaces the Python-flavoured `type_safety`, `dependency_health` and `test_strategy` prompts with TypeScript ones (unchecked `as`/`!`, unvalidated boundaries, hoisting-only imports, `vi.mock` of internals, type-level tests, ...). The TS review guidance's `patterns` list is split into `react` and `node` sections. The holistic context gets the toolchain's findings: `abstractions.type_errors` (tsc errors by code and file), `conventions.lint_rules` (linter findings by rule), `dependencies.manifest_issues` (Knip's unused/unlisted packages per manifest, not limited to reviewed files) and `testing.coverage` (verdicts by kind; with a coverage report, the reports and the lowest measured line coverage); batch prompts for the three dimensions point at them. Focus lines past `9j` were mangled by the prompt renumbering, now fixed. `convention_outlier` and `package_organization` still mention `.py`/`TypedDict` | S | DT-16 |
| 3.11 | **Done (#83).** Test health reads Istanbul `coverage-final.json` and `lcov.info` reports from `coverage/` in the root and in each package, plus a vitest `reportsDirectory` or jest `coverageDirectory`. Measured line coverage replaces the import-graph verdict file by file: 80% of lines passes, less is `low_coverage` weighted by the shortfall, no line run keeps `untested_*`. Files changed after the report, and files missing from it, keep the graph verdict; per-package reports merge by line. Istanbul HTML report assets are no longer scanned as source | M | DT-12 |
| 3.12 | **Done (#85).** `config set disabled <detector or mechanical dimension>` takes a detector or a whole dimension out of scoring: its potential and new issues are dropped, the dimension is recomputed (or disappears, not carried forward), and its existing issues are hidden with status unchanged (wontfix untouched). `config set`/`unset` rescore immediately; `config unset <key> <value>` removes one list entry. `status` lists what is disabled, `show <detector>` says so; re-enabling rechecks the hidden issues on the next scan | M | CE-15 |

### 2E. Engineering foundation

- **CI, done.** `tests-core` runs on Python 3.11–3.14, every version pyproject declares (3.14 classifier added); `tests-full` runs on 3.11 and 3.14 (E7). A `tests-windows` job runs the core suite on `windows-latest` (CE-16). It found real bugs, now fixed: subprocess output and source files were decoded with the locale encoding (cp1252), which broke the Codex runner's readers and shifted lines and columns in non-ASCII files; coverage discovery left backslash-separated graph keys; tsc/lint coverage notes used native separators. Every pytest run has a per-test `--timeout` (pytest-timeout, `PYTEST_TIMEOUT`, default 120s) (E12).
- **Lint and types:** enforce the configured ruff `E,F,I,B,UP` and `ruff format --check` (deferred: the whole-codebase fix and format sweep would conflict with every open PR, so it waits for a quiet moment). CI checks only `E9,F63,F7,F82` today, and some F401/F841 debt remains. Extend mypy past its 16 files into `languages/typescript` and `_framework`, ratcheting with per-module ignores. Add import-linter contracts that already hold: `languages` ↛ `app`, `engine` ↛ `app` (E6).
- **Dev tooling, done.** A `dev` extra pins pytest, pytest-xdist, pytest-timeout, ruff, mypy, import-linter, PyYAML, build and twine. `make install-dev` and `make install-full` install once. The gate targets (`lint`, `typecheck`, `arch`, `ci-contracts`, `tests*`, `package-smoke`) no longer `pip install`; CI and the publish job run the install target as a separate step. `tests-full` stops early without the `[full]` extra, and `install-hooks` works in a worktree. The release checklist's local-validation step now says to install first, and its description of the publish workflow includes the install step (E11).
- **Tests:**
  - done (#82): an autouse isolation fixture plus a guard that fails if the repo's `.desloppify/` is touched (E5);
  - done: the `inspect.getsource` and `callable(fn)` tripwire tests are gone (E9). Four of them checked import boundaries; those are now import-linter contracts (`engine` ↛ `app`, `languages` ↛ `app`, and `app`/`engine` reach `languages._framework` only through the `languages.framework` facade). The others either checked source text or only checked that a name exists. Since `callable()` never runs the function, none of them executed any code. Three things they were standing in for are now behaviour tests: the review session baseline and its drift reasons, the subjective-assessment store, and `cmd_deprecated --json`. Some functions were referenced only by tripwires and are run by no test at all, notably `render_plan_item`, `write_status_query`, `_show_concerns`, `cmd_plan_reorder` and `write_review_packet_snapshot`. `resolve_module` in `languages/typescript/detectors/deps/resolve.py` had no callers and is deleted;
  - done: deleted the duplicate `tests/review/integration/*` wrappers, which ran 178 tests a second time (E10);
  - done: `tests/lang/typescript/` folded into `languages/typescript/tests/` (E13).
- **Grammar preflight, done.** `desloppify setup --grammars` downloads the missing tsx and typescript grammars, loads each and fails naming any that will not load; the reduced-coverage warning now points at it. `is_available()` loads the tsx grammar once per process: when it cannot load, AST paths treat tree-sitter as absent and every call re-records the failure, so each scan still reports reduced coverage. Building the plugin checks only that the pack imports, so `--help` never fetches grammars (PK-2).
- **Dead code:**
  - done: the `dev` command, whose only action was `test-hermes` (AR-4);
  - done: `base/optional_deps`;
  - done: `intelligence/review/dimensions/metadata.py` and `metadata_legacy.py`, compat layers nothing imported;
  - done: `register_detector`/`unregister_detector`/`reset_registered_detectors` and the `on_detector_registered` callbacks. The registry is now the static catalog, so the CLI name cache and narrative tool map no longer refresh;
  - done: the cwd-relative reads. The cross-language security detector fell back to the cwd when given no scan root, and holistic review scoping resolved finder paths against the cwd; both now use the project root. The rest of `engine/` already goes through `resolve_path`/`resolve_scan_file`. `context_holistic/selection/contexts.py` (sibling-behaviour buckets) now resolves against the project root too. The framework-spec scanners (`frameworks/detection.py`, `nextjs/scanners.py`), `move` and the autofix preview read project files as UTF-8, no longer in the locale encoding.
- **Declarative detector spec (AR-1, L):** one dataclass covering DetectorMeta, zone policy, the phase and the detect command, registered by the plugin. That turns a new detector from a 6–8-file change into 2 files plus a test.
- **Scope (CE-8):** the plan, triage and review machinery is about 50k lines, much more than the TS plugin's 10k. Freeze it, collapse the state facades, replace the `work_items`/`issues` fallbacks with one accessor, and spend effort on detection accuracy.

### 2F. Fork identity and release

- **Distribution name (PK-4), done in #94.** PyPI name `desloppify-ts`, proposed version 1.1.0. The import package and the `desloppify` command are kept, plus a `desloppify-ts` alias so `uvx desloppify-ts` works. Publishing runs only on `release: published`, reuses CI as a gate and checks that the tag is `v<version>`. Left for the maintainer:
  - add the pypi.org trusted publisher for `desloppify-ts`;
  - set `PYPI_PUBLISH`;
  - cut the first release (`dev/release/RELEASE_CHECKLIST.md`).
- **Upstream pointers, done in #94.** `update-skill` installs the bundled SKILL.md. `docs/SKILL.md`, the README, `[project.urls]`, the runtime install hints and the release checklist now point at `cstarlea/desloppify` and `desloppify-ts`. Historical release notes are left as they were.
- **Attribution, done in #94.** The upstream LICENSE and copyright are kept. `NOTICE` names upstream, states that the fork modified the files, and credits every cherry-picked upstream PR author: the #744 batch with its 13 contributors, and #617, #629, #750 and #760. Keep merging with merge commits so authorship survives.
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
| FX-4 | high | `detect logs --fix` removes the first line only; tag regex matches strings, comments and `${}` | fixed (#1 removed the line deletion; 2.11 finds tagged calls on the syntax tree, #69) |
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
| TL-2 | high | Knip runs in the wrong dir, uses `pos` as line, silent when missing; drops categories | done (#1, 3.4) |
| TL-3 | high | tsc type errors never reported | done (#75, #76) |
| TL-4 | high | No ESLint/Biome/oxlint | done → 3.2 (#84, #87) |
| TL-5 | high | `next lint` removed in Next 16 | done (#1, upstream #760) |

### Graph, resolver and entry points

| ID | Sev | Title | Status |
|---|---|---|---|
| GR-1 | high | tsconfig: JSONC, references, extends, baseUrl, `@/` fallback | done (#1, #5, #8, #37, 2.34) |
| GR-2 | high | Workspaces and package exports not modelled | done (#5) |
| GR-3 | high | Missing require / import=require / triple-slash; `.mts`/`.cts` ignored | done (#3, #10) |
| GR-4 | medium | Comment and string imports create edges; `import type` creates false cycles | done (#3) |
| GR-5 | medium | Files with no imports never enter the graph | done (#1) |
| GR-6 | medium | No package.json or framework entries; loose dynamic match; Next 16 `proxy.ts` | done (#1, #3, #5, #39, 2.35, 2.36) |
| GR-8 | medium | Separate regex and tree-sitter graphs | dropped |
| GR-9 | medium | Results depend on the cwd | done (#6) |
| GR-10 | critical | Shared tree-sitter graph had zero edges | dropped (fixed in #1, deleted in #12) |

### Detectors

| ID | Sev | Title | Status |
|---|---|---|---|
| DT-1 | high | Global `matches[:50]` drops most smells | done (#1) |
| DT-2 | high | Body extractor grabs param or return-type braces | done (#1, upstream #629; #11 added tests) |
| DT-3 | low | Function extractor misses async/default/methods | done (#47, #60) |
| DT-4 | medium | Props detector counts lines, skips extends/generics/intersections | done (#61) |
| DT-5 | medium | Deprecated detector false positives; "safe to delete" on public API | done (#1, #65, #68) |
| DT-6 | low | Facade misses multi-line, `export * as`, `'use client'` | done (#5, #22, #36) |
| DT-7 | medium | eval/innerHTML false positives; comments not stripped | fixed (#1, #11, #57, #69) |
| DT-9 | medium | Non-null, block `@ts-ignore`, double-cast gaps | done (#54) |
| DT-10 | medium | Author-specific heuristics | open → 3.5 |
| DT-11 | medium | test-d, bench, e2e, config, generated not zoned | done (#1, 2.12) |
| DT-12 | high | Jest-only assertions; inverted test-health; cross-package basename mapping | done (#1, #8, #45, #48, #51, #56, #83) |
| DT-13 | medium | tsconfig strictness never read | fixed (3.3, `tsconfig_health`) |
| DT-14 | high | No framework support beyond Next.js (React Router entries only); SFCs unanalysed | mostly fixed: components analysed, Nuxt/Vue/SvelteKit/Astro entry conventions (3.7); React Router, NestJS, Express/Hono/Fastify and Angular specs with scanners (3.6); SvelteKit/Nuxt/Astro scanners open |
| DT-15 | medium | No server-action auth, raw-SQL or child_process checks | done (3.8) |
| DT-16 | low | Review prompts are Python-flavoured | done (3.10) |
| DT-17 | low | God rules are React-only | done (3.9) |

### Core engine, CLI and config

| ID | Sev | Title | Status |
|---|---|---|---|
| CE-1 | high | One plan.json across languages | dropped |
| CE-2 | high | Strict never recovers from real fixes | done (#38) |
| CE-3 | medium | One bad issue loses the whole state | done (#25, #29, #59) |
| CE-4 | medium | Unlocked read-modify-write | done (#35) |
| CE-5 | medium | Deferred, triaged_out and wontfix never auto-resolve | done (#42, #58) |
| CE-6 | medium | scoring.md and QUEUE_LIFECYCLE contradict code | done (#44) |
| CE-7 | low | Subjective scores taken as-is | open |
| CE-8 | low | Plan subsystem complexity | open → §2E |
| CE-9 | low | Carried-forward dimensions never expire | done (#62) |
| CE-10 | low | Concerns count suppressed issues | done (#62) |
| CE-11 | low | Recompute is O(dims × issues × modes) | open |
| CE-12 | medium | Headline score from zeroed subjective dims; ci profile; cycles under Security | done (#63, #64, #66) |
| CE-13 | medium | Exclusions applied after language and state resolution | dropped |
| CE-14 | medium | Auto-detect walks to an ancestor package.json | dropped |
| CE-15 | low | No detector or domain disable | done (#85) |
| CE-16 | medium | Codex runner Popen is locale-dependent on Windows | done (§2E: UTF-8 subprocess decoding, `tests-windows` job) |
| CE-17 | medium | JS ESLint on Windows | dropped |

### Tests, CI, packaging, architecture, upstream

| ID | Sev | Title | Status |
|---|---|---|---|
| E1 | high | No TS fixtures or e2e scan | done (#2) |
| E2 | high | tsc and knip always mocked; no Node in CI | done (#2) |
| E3 | high | CI red on main; PyPI publish ungated | done (#1, #4); name → §2F |
| E4 | medium | Glob-order test fails on tmpfs | done (#1, upstream #617) |
| E5 | medium | Tests write into the repo's `.desloppify` | done (#82) |
| E6 | low | Lint 4 codes, mypy 16 files, 1 import contract | partial (§2E: 4 import contracts); ruff and mypy → §2E |
| E7 | low | CI only on py3.11 | done (§2E: core on 3.11–3.14, full on 3.11 and 3.14) |
| E8 | medium | Ruby and R tests never collected | dropped |
| E9 | medium | getsource and callable tripwire tests | done (§2E) |
| E10 | low | Review tests run twice | done (§2E) |
| E11 | low | Make targets reinstall; release checklist drift | done (§2E: pinned `dev` extra, install-free gates; #94 fixed the checklist paths) |
| E12 | low | No test timeout | done (§2E: pytest-timeout, 120s per test) |
| E13 | low | TS tests in two trees | done (§2E) |
| PK-1 | medium | tree-sitter floor crashes; cap blocks working releases | done (#1) |
| PK-2 | medium | Offline grammar download silently drops findings | done (#1 reduced coverage; `setup --grammars`, grammar-loading `is_available()`) |
| PK-3 | medium | Wheel omits elixir/php/r review data | dropped |
| PK-4 | medium | Fork inherits PyPI name and upstream URLs | done (#4 gated publish, #94 `desloppify-ts` + release-triggered publish); first release → §2F |
| AR-1 | medium | New TS detector touches 6–8 files | open → §2E |
| AR-2 | medium | Regex-based TS plugin | partial (#3 imports on tree-sitter, #40 shared helper, 2.33 one parse per file) |
| AR-3 | medium | Plugin guide describes nonexistent files | done (#14) |
| AR-4 | low | Dead compat shims, `dev test-hermes` | done (#12 shims; §2E `dev`, `optional_deps`, metadata layers, runtime detector registration) |
| UP-1 | high | Upstream abandoned | — |
| UP-2 | high | #744 + #617 + #760 + #629 merge clean | done (#1) |
| UP-3 | medium | MercurialUroboros TS false-positive commits | open → §2F |
