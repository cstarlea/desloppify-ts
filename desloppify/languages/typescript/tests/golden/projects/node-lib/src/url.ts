export enum Method {
  Get = 'GET',
  Head = 'HEAD',
  Purge = 'PURGE',
}

export function buildUrl(base: string, path: string): string {
  return new URL(path, base).toString();
}

export const joinUrl = buildUrl;

/** @deprecated Renamed to buildUrl. */
export const makeUrl = buildUrl;
