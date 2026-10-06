import Link from 'next/link';

// Rendered by Next.js for forbidden() calls; never imported.
export default function Forbidden() {
  return (
    <main>
      <h2>Forbidden</h2>
      <p>You do not have access to this page.</p>
      <Link href="/">Return home</Link>
    </main>
  );
}
