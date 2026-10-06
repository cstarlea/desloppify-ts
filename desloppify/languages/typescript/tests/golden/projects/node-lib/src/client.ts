import { HttpError } from './errors.js';
import { withRetry, type RetryOptions } from './retry.js';

export async function tinyFetch(url: string, options: RetryOptions = {}): Promise<Response> {
  return withRetry(async () => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new HttpError(response.status, url);
    }
    return response;
  }, options);
}
