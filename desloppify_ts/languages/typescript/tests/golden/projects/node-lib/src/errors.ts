export class HttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly url: string,
  ) {
    super(`Request to ${url} failed with ${status}`);
  }
}

/** @deprecated Public API kept for v1 consumers; use HttpError.status. */
export function isClientError(error: HttpError): boolean {
  return error.status >= 400 && error.status < 500;
}
