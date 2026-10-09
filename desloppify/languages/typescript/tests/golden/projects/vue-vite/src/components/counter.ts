import { defineComponent, ref } from 'vue'

export default defineComponent({
  setup() {
    const count = ref(0)
    function increment(): void {
      count.value += 1
    }
    return { count, increment }
  },
})
