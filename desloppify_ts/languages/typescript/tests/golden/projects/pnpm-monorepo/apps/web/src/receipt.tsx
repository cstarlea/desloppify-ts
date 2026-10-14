import type { CartLine } from './cart';

// Imported only as "~/receipt": the alias lives in apps/web/tsconfig.json.
export function Receipt({ lines }: { lines: CartLine[] }) {
  return (
    <ol>
      {lines.map((line) => (
        <li key={line.sku}>{line.sku}</li>
      ))}
    </ol>
  );
}
