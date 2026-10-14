import { json } from '@sveltejs/kit'

export function GET() {
  const uptime = process.uptime()
  return json({ ok: true, uptime })
}
