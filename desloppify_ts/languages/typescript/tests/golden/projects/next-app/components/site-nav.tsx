import dynamic from 'next/dynamic';

const CartBadge = dynamic(() => import('@/components/cart-badge'));

// Shown on the docs page as a code sample; it is not an import.
const IMPORT_EXAMPLE = "import { OldHero } from './old-hero'";

export function SiteNav() {
  return (
    <nav>
      Shop <CartBadge />
      <code hidden>{IMPORT_EXAMPLE}</code>
    </nav>
  );
}
