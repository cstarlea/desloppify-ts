// Leaf module: imports nothing and nothing imports it.
export const MAX_TODOS = 500;
export const STORAGE_KEY = 'todos';
export const FILTERS = ['all', 'done'] as const;

export const LIMITS = {
  titleLength: 40,
  todos: MAX_TODOS,
};

export type Filter = (typeof FILTERS)[number];
