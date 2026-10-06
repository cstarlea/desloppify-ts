import type { Checkout } from './checkout';

export interface CartLine {
  sku: string;
  render?: typeof Checkout;
}
