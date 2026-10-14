<script lang="ts">
export default { name: 'SettingsView' }
</script>

<script setup lang="ts">
import { ref } from 'vue'

const session = useSession()
const theme = ref<'light' | 'dark'>('light')

function toggle(): void {
  if (theme.value === 'light') {
  } else {
  }
  theme.value = theme.value === 'light' ? 'dark' : 'light'
  session.save(theme.value)
}
</script>

<template>
  <button @click="toggle">{{ theme }}</button>
</template>
