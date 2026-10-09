import { HttpError } from './errors.js';
import { withRetry, type RetryOptions } from './retry.js';
import { Method, buildUrl, joinUrl } from './url.js';

export async function tinyFetch(url: string, options: RetryOptions = {}): Promise<Response> {
  return withRetry(async () => {
    const target = url.endsWith('/') ? joinUrl(url, '.') : buildUrl(url, '');
    const response = await fetch(target, { method: Method.Get });
    if (!response.ok) {
      throw new HttpError(response.status, url);
    }
    return response;
  }, options);
}
