// Resolved through package.json "main" (./dist/index.js), mapped back to src/
// by this package's tsconfig outDir/rootDir. Imports utils, which imports it
// back: a runtime cycle across two workspace packages.
import { formatMoney } from '@acme/utils/money';

export const currency = 'USD';

export function priceLabel(cents: number): string {
  return `Price: ${formatMoney(cents)}`;
}
