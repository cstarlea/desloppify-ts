import { describe, expect, it } from 'vitest';
import { formatReport } from '../src/report.js';

describe('formatReport', () => {
  it('reports the count and the average', () => {
    const report = formatReport([1, 2, 3]);
    expect(report).toContain('count:');
    expect(report).toContain('2.00');
  });

  it('handles an empty list', () => {
    expect(formatReport([])).toContain('0.00');
  });
});
