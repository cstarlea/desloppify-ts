export interface Post {
  slug: string
  title: string
  body: string
}

export async function getPosts(fetcher: typeof fetch): Promise<Post[]> {
  const response = await fetcher('/api/posts')
  return JSON.parse(await response.text())
}

export async function getPost(fetcher: typeof fetch, slug: string): Promise<Post> {
  const response = await fetcher(`/api/posts/${slug}`)
  return response.json()
}
