export interface Link {
  url: string;
  rel: string;
}

export function parseLinkHeader(header: string): Link[] {
  return header
    .split(',')
    .map((part) => part.trim().match(/^<([^>]+)>;\s*rel="([^"]+)"$/))
    .filter((match): match is RegExpMatchArray => match !== null)
    .map(([, url, rel]) => ({ url, rel }));
}
