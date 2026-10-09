# Configuration

Project settings live in `.desloppify/config.json`. `desloppify config show` lists the top-level keys; `desloppify config set <key> <value>` and `desloppify config unset <key> [<value>]` change them. TypeScript settings sit under `languages.typescript` and are edited in the file.

Nothing in the defaults assumes a directory layout, a naming scheme or a data library. Project-specific rules come from three places:

- **Frameworks** are detected from the nearest `package.json` (and config files such as `next.config.ts` or `supabase/config.toml`). A detected framework brings its entry-point conventions and checks.
- **Presets** are named, documented project layouts. They are off unless the `presets` key lists them, or the package depends on a linter that enforces that layout.
- **Settings** under `languages.typescript` describe what is yours alone: your layers, your competing hooks.

## Presets

```bash
desloppify config set presets bulletproof-react
desloppify config unset presets bulletproof-react
```

`presets` is a list. `config set` rejects unknown names and lists the known ones. Run `desloppify scan` afterwards to apply the change.

| Preset | Layers, top first | Turned on by |
|---|---|---|
| `feature-sliced` | [Feature-Sliced Design](https://feature-sliced.design/docs/reference/layers): `src/app`, `src/processes`\*, `src/pages`\*, `src/widgets`\*, `src/features`\*, `src/entities`\*, `src/shared` | `presets`, or a dependency on `steiger`, `@feature-sliced/steiger-plugin`, `@feature-sliced/eslint-config` or `@conarti/eslint-plugin-feature-sliced` |
| `bulletproof-react` | [Bulletproof React](https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md): `src/app`, `src/features`\*, then shared `src/{components,config,hooks,lib,stores,types,utils}` | `presets` only |

\* sliced: each subdirectory is one slice.

The `presets` key also accepts a framework's id (`nextjs`, `nuxt`, `vue`, `sveltekit`, `astro`, `react-router`, `nestjs`, `express`, `hono`, `fastify`, `angular`). That turns its checks on where detection misses it, for example when the dependency is declared in another workspace package. A framework's entry-point conventions still follow the package's own files.

When several layout presets are active, the first one listed wins.

## Layers

Layers drive the `coupling` detector. With no layers (the default) it reports nothing.

- **Layer violation** (tier 2): a file imports a layer above its own.
- **Cross-slice import** (tier 2): two slices of one sliced layer import each other. A slice's `cross_import_dir` (Feature-Sliced Design's `@x`) is the exception.
- **Boundary candidate** (tier 3): a file in an unsliced layer that only one slice imports, so it could move into that slice. Barrel files and a shadcn/ui kit directory are skipped. Files `single_use` already reports are skipped too.

To describe your own layout, set `languages.typescript.layers`. It replaces any preset's layers.

```json
{
  "languages": {
    "typescript": {
      "layers": [
        {"name": "app", "paths": ["src/app"]},
        {"name": "tools", "paths": ["src/tools"], "sliced": true},
        {"name": "shared", "paths": ["src/shared"]}
      ]
    }
  }
}
```

Paths are directory prefixes, relative to the project root. The longest matching prefix decides a file's layer, so `src/shared/ui` can be its own layer above `src/shared`. Files outside every layer are ignored.

## Pattern families

The `patterns` detector reports an area (a directory two levels deep) that mixes competing approaches to one job. Which hooks compete is project knowledge, so no family is built in. Define your own families:

```json
{
  "languages": {
    "typescript": {
      "pattern_families": {
        "settings_persistence": {
          "description": "Standardise on one settings hook",
          "threshold": 2,
          "patterns": {
            "useAutoSaveSettings": "\\buseAutoSaveSettings\\s*[<(]",
            "useToolSettings": "\\buseToolSettings\\s*[<(]"
          }
        }
      }
    }
  }
}
```

An area is reported when it uses `threshold` (default 2) or more patterns of a family, or a pattern that fewer than 10% of areas use. Analysis needs at least five areas that use some pattern. A family with `"type": "complementary"` is only counted in `desloppify detect patterns`.

## shadcn/ui

When the project root has a shadcn/ui `components.json`, the UI kit directory it names (`aliases.ui`, by default `@/components/ui`, resolved against `src/` or the root) is vendored code. The `naming` detector skips it, and so do boundary candidates.

## Other TypeScript settings

| Setting | Default | Meaning |
|---|---|---|
| `auth_functions` | `[]` | Extra function names that count as an auth check in server actions and route handlers |
| `lint_type_aware_max_files` | `400` | Most files to lint when the ESLint or XO config is type-aware (0 = no limit) |
