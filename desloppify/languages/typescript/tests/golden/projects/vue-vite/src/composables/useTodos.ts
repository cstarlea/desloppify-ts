import { ref } from 'vue'
import type { Todo } from '../types'

export function useTodos() {
  const todos = ref<Todo[]>([])
  let next = 1

  function add(title: string): void {
    todos.value.push({ id: next, title, done: false })
    next += 1
  }

  return { todos, add }
}
