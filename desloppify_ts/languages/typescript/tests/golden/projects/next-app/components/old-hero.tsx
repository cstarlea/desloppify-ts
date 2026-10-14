interface OldHeroProps {
  headline: string;
  cta: string;
}

// Replaced by the product grid; nothing renders it any more.
export function OldHero({ headline, cta }: OldHeroProps) {
  return (
    <section className="hero">
      <h1>{headline}</h1>
      <button type="button">{cta}</button>
    </section>
  );
}
