import { getProducts } from '@/lib/products';

export async function GET() {
  return Response.json(await getProducts());
}
