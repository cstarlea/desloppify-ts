import { notify } from '@/lib/notifications';

let count = 0;

export function getCount(): number {
  return count;
}

export function increment(): void {
  count += 1;
  notify(`count is ${count}`);
}
