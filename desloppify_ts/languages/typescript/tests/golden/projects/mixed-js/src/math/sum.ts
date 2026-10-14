// Converted to TypeScript; still imported only from JavaScript files.
export function sum(values: readonly number[]): number {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

export function sumOfSquares(values: readonly number[]): number {
  return sum(values.map((value) => value * value));
}
