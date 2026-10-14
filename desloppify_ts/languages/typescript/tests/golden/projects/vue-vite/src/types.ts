export interface Todo {
  id: number
  title: string
  done: boolean
}

export type Filter = 'all' | 'open' | 'done'
