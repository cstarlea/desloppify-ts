import { getPost } from '$lib/api'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ fetch, params }) => {
  const post = await getPost(fetch, params.slug)
  return { post }
}
