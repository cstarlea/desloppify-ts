import type { Todo } from '@/types';
import { formatTitle } from '@/lib/format';

export function TodoList({ todos, onToggle }: { todos: Todo[]; onToggle: (id: string) => void }) {
  return (
    <ul>
      {todos.map((todo) => (
        <li key={todo.id} onClick={() => onToggle(todo.id)}>
          {formatTitle(todo.title)}
        </li>
      ))}
    </ul>
  );
}
