// Tests apps/web's cart typing. Its name matches packages/utils/src/dates.ts,
// but a test in one package says nothing about another package's files.
import { describe, expect, it } from 'vitest';
import type { CartLine } from '../src/cart';

describe('cart lines', () => {
  it('keeps the sku', () => {
    const line: CartLine = { sku: 'A-1' };
    expect(line.sku).toBe('A-1');
  });
});
