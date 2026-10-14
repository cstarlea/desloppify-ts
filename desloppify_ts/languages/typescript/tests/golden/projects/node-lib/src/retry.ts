export interface RetryOptions {
  retries?: number;
  delayMs?: number;
}

export async function withRetry<T>(fn: () => Promise<T>, { retries = 2, delayMs = 100 }: RetryOptions = {}): Promise<T> {
  let lastError: unknown;
  const startedAt = Date.now();
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, delayMs * 2 ** attempt));
    }
  }
  throw lastError;
}
