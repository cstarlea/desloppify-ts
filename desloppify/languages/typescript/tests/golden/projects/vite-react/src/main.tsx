import { createRoot } from 'react-dom/client';
import { App } from '@/App';
import { track } from '#lib/analytics';

// Widgets register themselves; Vite bundles every match of the glob.
import.meta.glob('./widgets/*.tsx', { eager: true });

export function loadLocale(lang: string) {
  return import(`./locales/${lang}.ts`);
}

track('boot');
createRoot(document.getElementById('root')!).render(<App />);
