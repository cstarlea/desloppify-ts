<script setup lang="ts">
import type { Todo } from '../types'

const props = defineProps<{ items: Todo[] }>()

function label(todo: Todo): string {
  const extra = (todo as any).note ?? ''
  return `${todo.title} ${extra}`.trim()
}
</script>

<template>
  <ul>
    <li v-for="todo in props.items" :key="todo.id">{{ label(todo) }}</li>
  </ul>
</template>
