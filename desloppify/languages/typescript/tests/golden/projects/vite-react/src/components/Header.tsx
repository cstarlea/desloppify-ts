import { useMemo } from 'react';

interface HeaderProps {
  title: string;
  onFilter: (filter: 'all' | 'done') => void;
}

export function Header({ title, onFilter }: HeaderProps) {
  return (
    <header>
      <h1>{title}</h1>
      <button onClick={() => onFilter('all')}>All</button>
      <button onClick={() => onFilter('done')}>Done</button>
    </header>
  );
}
