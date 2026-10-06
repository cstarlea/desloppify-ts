#!/usr/bin/env node
// Package "bin" entry: run by npm, never imported.
import { tinyFetch } from './index.js';

const [url] = process.argv.slice(2);
if (!url) {
  process.stderr.write('usage: tiny-fetch <url>\n');
  process.exit(1);
}
const response = await tinyFetch(url);
process.stdout.write(await response.text());
