import type { Handle } from '@sveltejs/kit'

export const handle: Handle = async ({ event, resolve }) => {
  const started = Date.now()
  const response = await resolve(event)
  response.headers.set('x-time', String(Date.now() - started))
  return response
}
