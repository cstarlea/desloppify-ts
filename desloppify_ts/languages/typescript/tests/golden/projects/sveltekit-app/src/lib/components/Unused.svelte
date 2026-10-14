<script lang="ts">
  let { message }: { message: string } = $props()

  function loud(text: string): string {
    return text.toUpperCase()
  }
</script>

<p>{loud(message)}</p>
<p>Nothing renders this component.</p>
<p>It is an orphan.</p>
