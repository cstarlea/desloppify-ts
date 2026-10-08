# Languages

Desloppify scans TypeScript and JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`,
`.mjs`, `.cjs`, ...) through one plugin, `typescript/`. There is no plugin
discovery or registry: `desloppify.languages.framework.default_lang()` builds
the `TypeScriptConfig` once and every command uses it.

## Layout

```
languages/
├── framework.py           # Public facade: default_lang(), get_lang(), get_lang_hook(), LangRun, types
├── typescript/            # The plugin
│   ├── __init__.py        # TypeScriptConfig (phases, fixers, zones, review hooks) + LANG_HOOKS
│   ├── phases_*.py        # Detector phase runners
│   ├── detectors/         # TS/JS detectors (deps, exports, unused, smells, react, security, ...)
│   ├── fixers/            # Auto-fixers
│   ├── commands.py        # `desloppify detect <name>` registry
│   ├── move.py            # Import rewriting for `desloppify move`
│   ├── review.py          # Review guidance, holistic dimensions, migration patterns
│   ├── test_coverage.py   # Test-coverage hooks (looked up via get_lang_hook)
│   └── tests/             # Plugin tests, including golden fixtures
└── _framework/            # Shared building blocks the plugin is assembled from
    ├── base/              # LangConfig, DetectorPhase, FixerConfig, shared phases
    ├── commands/          # Shared detect-command factories
    ├── frameworks/        # Framework detection + phases (Next.js)
    ├── node/              # Node/JS helpers and Next.js scanners
    ├── runtime_support/   # LangRun (per-run mutable execution state)
    ├── tools/             # External tool runner + output parsers
    ├── treesitter/        # Tree-sitter parsing, cache, cohesion phase
    └── review_data/       # Shared review dimension JSON payloads
```

## Public Runtime Facade

App and engine code imports language access from `desloppify.languages.framework`
(`default_lang()`, `make_lang_run`, `LangRun`, `DetectorPhase`, parse-cache
helpers), not from `desloppify.languages._framework.*`. The plugin itself uses
`_framework` internals directly.

`get_lang(name)` resolves a language name stored in state or config and raises
`ValueError` for anything but `typescript`. State files written for JavaScript
before the TypeScript-only fork are relabelled on migration.

## Design Rules

- Import direction: `languages/typescript/` → `engine/detectors/` and
  `languages/_framework/*`. Never the reverse.
- Keep TS/JS-specific code in `typescript/`; keep `_framework/` for pieces
  shared by the plugin's phases and by the engine.

## Testing

```bash
# Language framework tests
pytest -q desloppify/tests/lang/common/

# Plugin tests
pytest -q desloppify/languages/typescript/tests/
```
