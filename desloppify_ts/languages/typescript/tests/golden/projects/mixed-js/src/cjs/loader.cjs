'use strict';

// CommonJS module imported from ESM.
const path = require('node:path');

function loadNumbers(args) {
  return args
    .map((arg) => path.basename(arg))
    .map(Number)
    .filter((value) => Number.isFinite(value));
}

module.exports = { loadNumbers };
