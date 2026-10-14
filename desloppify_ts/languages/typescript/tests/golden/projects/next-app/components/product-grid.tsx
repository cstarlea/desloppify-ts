import type { Product } from '@/lib/types';

export function ProductGrid({ products }: { products: Product[] }) {
  return (
    <section>
      {products.map((p) => (
        <article key={p.id} dangerouslySetInnerHTML={{ __html: p.descriptionHtml }} />
      ))}
    </section>
  );
}
