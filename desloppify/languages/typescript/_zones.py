"""Zone/path classification rules for TypeScript."""

from __future__ import annotations

from desloppify.engine.policy.zones import COMMON_ZONE_RULES, Zone, ZoneRule

TS_ZONE_RULES = [
    ZoneRule(
        Zone.GENERATED,
        [
            ".d.ts",
            ".d.mts",
            ".d.cts",
            "/migrations/",
            ".gen.",  # TanStack Router routeTree.gen.ts, codegen output
            ".generated.",
            "_pb.ts",  # protobuf-ts / ts-proto
            "_pb.js",
            "/gql/",  # graphql-codegen client preset default output
            # Capacitor's `cap sync` copies of the web build
            "/ios/App/App/public/",
            "/android/app/src/main/assets/public/",
        ],
    ),
    ZoneRule(
        Zone.TEST,
        [
            "/__tests__/",
            ".test.",
            ".spec.",
            ".stories.",
            "/__mocks__/",
            "/__fixtures__/",
            "setupTests.",
            # Type-level tests (tsd, vitest typecheck)
            ".test-d.",
            "/test-d/",
            # End-to-end suites
            "/e2e/",
            ".e2e.",
            "/cypress/",
            ".cy.",
            "/playwright/",
            # Benchmarks are harnesses, not shipped code
            "/bench/",
            "/benchmark/",
            "/benchmarks/",
            ".bench.",
            "vitest.setup",
            "jest.setup",
        ],
    ),
    ZoneRule(
        Zone.CONFIG,
        [
            "vite.config",
            "tailwind.config",
            "postcss.config",
            "tsconfig",
            "eslint",
            "prettier",
            "jest.config",
            "vitest.config",
            "vitest.workspace",
            "next.config",
            "webpack.config",
            "rollup.config",
            "tsup.config",
            "playwright.config",
            "cypress.config",
            "astro.config",
            "svelte.config",
            "nuxt.config",
            "remix.config",
            "react-router.config",
            "drizzle.config",
            "babel.config",
            "commitlint.config",
            "knip.config",
        ],
    ),
    # Any tool's config file: vitest.integration.config.ts, capacitor.config.ts.
    # Angular's src/app/app.config.ts and Nuxt's app.config.ts are app code.
    ZoneRule(
        Zone.CONFIG,
        [f"*.config.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")],
        exclude=["app.config.*"],
    ),
] + COMMON_ZONE_RULES

__all__ = ["TS_ZONE_RULES"]
