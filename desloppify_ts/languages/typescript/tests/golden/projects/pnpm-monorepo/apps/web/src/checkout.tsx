import { isoDay } from '@acme/utils/dates';
import type { CartLine } from './cart';

export function Checkout({ total, lines = [] }: { total: string; lines?: CartLine[] }) {
  return <p>{lines.length} items, {total} on {isoDay(new Date())}</p>;
}
