<script lang="ts">
  let { data } = $props()
</script>

<article>
  <h1>{data.post.title}</h1>
  {@html data.post.body}
</article>
