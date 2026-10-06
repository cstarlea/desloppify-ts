import { useState } from 'react';
import { Header } from '@/components/Header';
import { TodoList } from '@/components/TodoList';
import { useTodos } from '@/hooks/useTodos';
import { increment } from '@/lib/store';
// import { LegacyBanner } from '@/components/LegacyBanner';

export function App() {
  const [filter, setFilter] = useState<'all' | 'done'>('all');
  const { todos, toggle } = useTodos();
  return (
    <main>
      <Header title="Todos" onFilter={(f) => { increment(); setFilter(f); }} />
      <TodoList todos={todos.filter((t) => filter === 'all' || t.done)} onToggle={toggle} />
    </main>
  );
}
