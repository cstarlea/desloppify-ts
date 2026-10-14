import { ProductGrid } from 'components/product-grid';
import { getProducts } from '@/lib/products';

export default async function Page() {
  const products = await getProducts();
  return <ProductGrid products={products} />;
}
