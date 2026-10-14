<script lang="ts">
  import Nav from '$lib/components/Nav.svelte'

  let { children } = $props()
  const links = [
    { href: '/', label: 'Home' },
    { href: '/blog', label: 'Blog' },
  ]
</script>

<Nav {links} />
{@render children()}
