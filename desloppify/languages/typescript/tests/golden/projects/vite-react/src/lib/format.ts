/** @deprecated use Intl.Segmenter-based truncate() instead */
export function formatTitle(title: string): string {
  return title.length > 40 ? `${title.slice(0, 40)}…` : title;
}
