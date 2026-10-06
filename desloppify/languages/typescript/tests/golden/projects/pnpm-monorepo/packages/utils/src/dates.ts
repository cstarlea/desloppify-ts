export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}
