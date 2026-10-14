import { registerWidget } from '@/lib/widgets';

interface ClockProps {
  compact?: boolean;
}

function Clock({ compact = false }: ClockProps) {
  return (
    <section className={compact ? 'widget widget--compact' : 'widget'}>
      <h2>Clock</h2>
      <p>Current time</p>
    </section>
  );
}

registerWidget('clock', Clock);
