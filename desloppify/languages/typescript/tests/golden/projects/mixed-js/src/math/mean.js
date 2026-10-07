import { sum } from './sum';

/**
 * Arithmetic mean; 0 for an empty list.
 * @param {number[]} values
 */
export function mean(values) {
  if (values.length === 0) {
    return 0;
  }
  return sum(values) / values.length;
}
