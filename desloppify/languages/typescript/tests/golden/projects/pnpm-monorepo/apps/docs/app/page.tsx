import { priceLabel } from '@acme/config';

const SECTIONS = ['Install', 'Configure', 'Deploy'];

export default function DocsHome() {
  return (
    <section>
      <h1>Docs</h1>
      <p>{priceLabel(4900)} per seat</p>
      <ul>
        {SECTIONS.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
    </section>
  );
}
