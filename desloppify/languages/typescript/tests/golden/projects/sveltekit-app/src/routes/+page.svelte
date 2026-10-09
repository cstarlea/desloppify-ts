<script module lang="ts">
  export const prerender = true
</script>

<script lang="ts">
  import { browser } from '$app/environment'

  let { data } = $props()
  if (browser) {
    console.log('[Home] posts', data.posts.length)
  }
</script>

<h1>Posts</h1>
{#each data.posts as post}
  <a href="/blog/{post.slug}">{post.title}</a>
{/each}
