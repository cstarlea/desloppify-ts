import type { ComponentType } from 'react';

type Widget = ComponentType<{ compact?: boolean }>;

const registry = new Map<string, Widget>();

export function registerWidget(name: string, component: Widget) {
  registry.set(name, component);
  return () => registry.delete(name);
}
