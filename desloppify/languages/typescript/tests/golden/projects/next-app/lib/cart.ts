import { useSyncExternalStore } from 'react';

let items: string[] = [];
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useCart() {
  return { items: useSyncExternalStore(subscribe, () => items) };
}
