import type { Todo } from '@/types';

export async function loadTodos(): Promise<Todo[]> {
  const raw = localStorage.getItem('todos');
  return raw ? (JSON.parse(raw) as any) : [];
}
