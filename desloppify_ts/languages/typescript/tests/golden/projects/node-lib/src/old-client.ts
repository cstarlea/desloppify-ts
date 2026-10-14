// Superseded by client.ts and imported by nothing: a real orphan, even though
// this package's manifest names entries (exports, bin, scripts).
export interface LegacyOptions {
  baseUrl: string;
  timeoutMs?: number;
}

export async function legacyFetch(path: string, options: LegacyOptions): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 5000);
  try {
    const response = await fetch(options.baseUrl + path, { signal: controller.signal });
    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}
