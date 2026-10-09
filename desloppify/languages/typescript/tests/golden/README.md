# TypeScript golden scans

End-to-end regression tests: each project in `projects/` is copied to a temp
directory and scanned with every mechanical TypeScript detector. The
normalized output (issue ids, tiers, zones, summaries, potentials, reduced
coverage) is compared with `snapshots/<project>.json`.

| Project | What it covers |
|---|---|
| `vite-react` | Solution-style tsconfig with `references`, JSONC, `@/` alias in `tsconfig.app.json`, vitest tests, a real runtime cycle, true orphans, tagged debug logs, `as any` |
| `next-app` | Next.js 16 (`proxy.ts`, `mdx-components.tsx`, `forbidden.tsx`, route handlers, server actions), `baseUrl` bare imports, `dangerouslySetInnerHTML` |
| `node-lib` | NodeNext `./x.js` specifiers, package `exports`/`bin`, AVA tests, deprecated public API, `page.$eval` (not `eval`) |
| `pnpm-monorepo` | `pnpm-workspace.yaml`, workspace package imports, `paths` inherited through `extends`, a type-only import cycle |
| `mixed-js` | JavaScript project mid-migration (`allowJs`): a `.ts` file imported only from JS, extensionless and `index.js` imports, a `.cjs` module, a JS test, a JS orphan, a minified bundle that must be ignored |
| `vue-vite` | Vue SFCs (`<script setup lang="ts">`, `<script>` + `<script setup>`, `<script src>`, a template-only page): smells, logs and line numbers in `.vue`, components imported only by components, an orphan component, an `unplugin-auto-import` registry, tsc's shim-less `import './X.vue'` |
| `sveltekit-app` | SvelteKit routes (`+page.svelte`, `+page.server.ts`, `+server.ts`), hooks and param matchers, `$lib`/`$app` imports, a module script, an orphan component, a tsconfig extending the generated `.svelte-kit/tsconfig.json` |

`expectations.json` holds the intent behind each project: findings that must
or must not appear, plus **known false positives / negatives** that are still
outstanding, each with the roadmap item that should fix it. Those lists are
strict: when a fix lands, the test fails until the entry is moved to
`must_not_find` / `must_find`, so the lists can't go stale.

## Layers

- **hermetic** (runs whenever tree-sitter is installed): `PATH` is reduced to
  `git`, so tsc/knip/eslint are unavailable and the snapshot records reduced
  coverage for them. Results don't depend on the machine.
- **node** (opt-in): links pinned `typescript` and `knip` from `node/` into the
  project and adds only the `node` binary to `PATH`. Install the tools once:

  ```bash
  npm ci --prefix desloppify/languages/typescript/tests/golden/node
  ```

  CI runs this layer in the `tests-golden-node` job (`make tests-golden-node`).

## Updating snapshots

After an intended behavior change:

```bash
DESLOPPIFY_UPDATE_GOLDEN=1 pytest desloppify/languages/typescript/tests/test_ts_golden.py
git diff desloppify/languages/typescript/tests/golden/snapshots
```

Review the snapshot diff like code: every added or removed finding should be
one you meant to change. Run the node layer too when the change touches tsc
or knip handling.

## Adding a project

Add a directory under `projects/` (keep it small and realistic; orphan
candidates need at least 10 lines or the detector ignores them), add its
entry to `expectations.json`, then generate snapshots as above.
