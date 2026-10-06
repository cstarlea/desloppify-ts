import { createRoot } from 'react-dom/client';
import { App } from '@/App';

// Widgets register themselves; Vite bundles every match of the glob.
import.meta.glob('./widgets/*.tsx', { eager: true });

export function loadLocale(lang: string) {
  return import(`./locales/${lang}.ts`);
}

createRoot(document.getElementById('root')!).render(<App />);
