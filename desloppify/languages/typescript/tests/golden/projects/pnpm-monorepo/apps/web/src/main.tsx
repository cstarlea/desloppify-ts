import { Button, Card } from '@acme/ui';
import { formatMoney } from '@acme/utils/money';
import { Checkout } from './checkout';

export function Main() {
  return (
    <Card>
      <Checkout total={formatMoney(1999)} />
      <Button label="Pay" />
    </Card>
  );
}
