import test from 'ava';
import { withRetry } from '../src/retry.js';

test('retries until success', async (t) => {
  let calls = 0;
  const result = await withRetry(async () => {
    calls++;
    if (calls < 2) throw new Error('flaky');
    return 'ok';
  }, { delayMs: 0 });
  t.is(result, 'ok');
  t.is(calls, 2);
});

test('gives up after retries', async (t) => {
  await t.throwsAsync(withRetry(async () => { throw new Error('down'); }, { retries: 1, delayMs: 0 }));
});
