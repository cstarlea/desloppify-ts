<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <header>
    <h1>{{ title }}</h1>
    <nav>
      <RouterLink to="/">Home</RouterLink>
      <RouterLink to="/about">About</RouterLink>
      <RouterLink to="/settings">Settings</RouterLink>
    </nav>
  </header>
</template>
