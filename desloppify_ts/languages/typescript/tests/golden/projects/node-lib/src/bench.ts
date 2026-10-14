// Run by the "bench" script in package.json, never imported.
import { tinyFetch } from './index.js';

const [url, count = '5'] = process.argv.slice(2);
if (!url) throw new Error('usage: bench <url> [runs]');
const runs = Number(count);
const started = performance.now();
for (let i = 0; i < runs; i++) {
  await tinyFetch(url);
}
const elapsed = performance.now() - started;
process.stdout.write(`${(elapsed / runs).toFixed(1)}ms per request\n`);
