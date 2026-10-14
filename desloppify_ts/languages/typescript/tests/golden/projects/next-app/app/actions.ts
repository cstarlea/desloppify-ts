'use server';

import { saveProduct } from '@/lib/products';

export async function createProduct(formData: FormData) {
  await saveProduct(String(formData.get('name')));
}
