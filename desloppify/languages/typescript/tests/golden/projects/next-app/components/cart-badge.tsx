'use client';

import { useCart } from '@/lib/cart';

export default function CartBadge() {
  const { items } = useCart();
  if (items.length === 0) {
    return null;
  }
  return <span className="badge">{items.length}</span>;
}
