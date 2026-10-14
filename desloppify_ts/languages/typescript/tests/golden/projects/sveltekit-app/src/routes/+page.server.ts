import { getPosts } from '$lib/api'
import type { PageServerLoad } from './$types'

export const load: PageServerLoad = async ({ fetch }) => {
  const posts = await getPosts(fetch)
  return { posts }
}
