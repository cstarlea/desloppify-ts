<script setup lang="ts">
defineProps<{ heading: string; body: string }>()

function shout(text: string): string {
  return text.toUpperCase()
}
</script>

<template>
  <article>
    <h2>{{ shout(heading) }}</h2>
    <p>{{ body }}</p>
  </article>
</template>
