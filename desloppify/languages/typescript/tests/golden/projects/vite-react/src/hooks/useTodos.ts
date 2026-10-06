import { useEffect, useState } from 'react';
import type { Todo } from '@/types';
import { loadTodos } from '@/lib/storage';

export function useTodos() {
  const [todos, setTodos] = useState<Todo[]>([]);
  useEffect(() => {
    loadTodos().then((items) => {
      console.log('[TODOS] loaded', items.length);
      setTodos(items);
    });
  }, []);
  const toggle = (id: string) =>
    setTodos((items) => items.map((t) => (t.id === id ? { ...t, done: !t.done } : t)));
  return { todos, toggle };
}
