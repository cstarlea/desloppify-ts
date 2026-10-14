import { NextResponse, type NextRequest } from 'next/server';

// Next.js 16 entry point (renamed from middleware.ts); never imported.
export function proxy(request: NextRequest) {
  const session = request.cookies.get('session');
  if (!session) {
    return NextResponse.redirect(new URL('/login', request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ['/account/:path*'] };
