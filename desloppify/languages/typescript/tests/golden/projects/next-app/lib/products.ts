import type { Product } from '@/lib/types';

const API = 'https://api.example.com/v1/products';

export async function getProducts(): Promise<Product[]> {
  const res = await fetch(API);
  return res.json();
}

export async function saveProduct(name: string): Promise<void> {
  await fetch(API, { method: 'POST', body: JSON.stringify({ name }) });
}
