<script setup lang="ts">
import { computed } from 'vue'
import TodoList from '../components/TodoList.vue'
import { useTodos } from '../composables/useTodos'

const { todos, add } = useTodos()
const open = computed(() => todos.value.filter((todo) => !todo.done))

function addDefault() {
  add('New todo')
  console.log('[Home] added', todos.value.length)
}
</script>

<template>
  <main>
    <!-- console.log('[Template] not code') -->
    <p>{{ open.length }} open</p>
    <button @click="addDefault">Add</button>
    <TodoList :items="todos" />
  </main>
</template>
