import { getCount } from '@/lib/store';

const listeners: Array<(message: string) => void> = [];

export function subscribe(listener: (message: string) => void): void {
  listeners.push(listener);
}

export function notify(message: string): void {
  listeners.forEach((listener) => listener(`${message} (${getCount()})`));
}
