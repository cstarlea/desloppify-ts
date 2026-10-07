import { currency } from '@acme/config';

export function formatMoney(cents: number, code = currency): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: code }).format(cents / 100);
}
