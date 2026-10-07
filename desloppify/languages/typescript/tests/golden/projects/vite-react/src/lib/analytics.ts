// Imported only through the package.json "imports" alias (#lib/*), which the
// resolver doesn't map yet (roadmap 1.1), so this looks orphaned.
type Event = { name: string; at: number };

const queue: Event[] = [];

export function track(name: string): void {
  queue.push({ name, at: Date.now() });
}

export function flush(): Event[] {
  return queue.splice(0, queue.length);
}
