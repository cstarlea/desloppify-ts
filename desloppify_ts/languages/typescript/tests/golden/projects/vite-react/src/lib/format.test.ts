import { describe, expect, it } from 'vitest';
import { formatTitle } from './format';

describe('formatTitle', () => {
  it('keeps short titles', () => {
    expect(formatTitle('hi')).toBe('hi');
  });

  it.each([[41], [80]])('truncates %i chars', (n) => {
    expect(formatTitle('x'.repeat(n)).length).toBe(41);
  });
});
