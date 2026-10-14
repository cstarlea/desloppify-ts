import { Button, Card } from '@acme/ui';
import { formatMoney } from '@acme/utils/money';
import { Checkout } from './checkout';
// "~/" is an alias in apps/web/tsconfig.json only, not in the root config.
import { Receipt } from '~/receipt';

export function Main() {
  return (
    <Card>
      <Checkout total={formatMoney(1999)} />
      <Receipt lines={[]} />
      <Button label="Pay" />
    </Card>
  );
}
