"""Zone classification for common TypeScript project layouts."""

from __future__ import annotations

import pytest

from desloppify.engine.policy.zones import Zone, classify_file
from desloppify.languages.typescript._zones import TS_ZONE_RULES


@pytest.mark.parametrize(
    ("path", "zone"),
    [
        # Generated output
        ("src/routeTree.gen.ts", Zone.GENERATED),
        ("src/api/client.generated.ts", Zone.GENERATED),
        ("src/gql/graphql.ts", Zone.GENERATED),
        ("proto/user_pb.ts", Zone.GENERATED),
        ("types/index.d.mts", Zone.GENERATED),
        # Tests, type tests, e2e and benchmarks
        ("test-d/index.test-d.ts", Zone.TEST),
        ("src/api.test-d.ts", Zone.TEST),
        ("e2e/login.spec.ts", Zone.TEST),
        ("cypress/e2e/checkout.cy.ts", Zone.TEST),
        ("tests/playwright/auth.ts", Zone.TEST),
        ("packages/bench/index.ts", Zone.TEST),
        ("src/sort.bench.ts", Zone.TEST),
        ("src/__fixtures__/user.ts", Zone.TEST),
        # Tool config
        ("playwright.config.ts", Zone.CONFIG),
        ("tsup.config.ts", Zone.CONFIG),
        ("astro.config.mjs", Zone.CONFIG),
        ("drizzle.config.ts", Zone.CONFIG),
        # Lookalikes that are real code
        ("src/app/app.config.ts", Zone.PRODUCTION),
        ("src/generator.ts", Zone.PRODUCTION),
        ("src/benchmarking-utils.ts", Zone.PRODUCTION),
        ("src/cyclic.ts", Zone.PRODUCTION),
    ],
)
def test_ts_zone_classification(path, zone):
    assert classify_file(path, TS_ZONE_RULES) == zone
