// Superseded by src/report.js; nothing imports this any more.
export function oldFormatReport(values) {
  let total = 0;
  for (let i = 0; i < values.length; i++) {
    total += values[i];
  }
  const average = values.length ? total / values.length : 0;
  const lines = [];
  lines.push('count: ' + values.length);
  lines.push('average: ' + average.toFixed(2));
  lines.push('total: ' + total);
  return lines.join('\n');
}

export function oldHeader() {
  return '== report ==';
}
