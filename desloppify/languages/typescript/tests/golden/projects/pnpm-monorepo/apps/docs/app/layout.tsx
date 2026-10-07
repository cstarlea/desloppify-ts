// A Next.js app inside the monorepo: next.config.ts is in apps/docs, not at
// the scan root, so App Router conventions must be detected per package.
export const metadata = { title: 'Acme docs' };

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <main>{children}</main>
      </body>
    </html>
  );
}
