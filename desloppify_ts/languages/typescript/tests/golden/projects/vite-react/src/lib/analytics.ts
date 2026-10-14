// Imported only through the package.json "imports" alias (#lib/*); it looks
// orphaned unless the resolver maps `#` subpaths.
type Event = { name: string; at: number };

const queue: Event[] = [];

export function track(name: string): void {
  queue.push({ name, at: Date.now() });
}

export function flush(): Event[] {
  return queue.splice(0, queue.length);
}
