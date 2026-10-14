#!/usr/bin/env node
// Package "bin" entry: run by npm, never imported.
import loader from './cjs/loader.cjs';
import { formatReport } from './report.js';

const values = loader.loadNumbers(process.argv.slice(2));
process.stdout.write(`${formatReport(values)}\n`);
