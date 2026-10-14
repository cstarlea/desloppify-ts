import { registerWidget } from '@/lib/widgets';

interface WeatherProps {
  compact?: boolean;
}

function Weather({ compact = false }: WeatherProps) {
  return (
    <section className={compact ? 'widget widget--compact' : 'widget'}>
      <h2>Weather</h2>
      <p>Current forecast</p>
    </section>
  );
}

registerWidget('weather', Weather);
