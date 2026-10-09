<script lang="ts">
  import { page } from '$app/state'

  let { links }: { links: { href: string; label: string }[] } = $props()
</script>

<nav>
  {#each links as link}
    <a href={link.href} aria-current={page.url.pathname === link.href}>{link.label}</a>
  {/each}
</nav>
