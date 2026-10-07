import { mean } from './math/index.js';
import { pad } from './format';

export function formatReport(values) {
  const average = mean(values);
  const lines = [
    `count:   ${pad(String(values.length), 8)}`,
    `average: ${pad(average.toFixed(2), 8)}`,
  ];
  return lines.join('\n');
}
