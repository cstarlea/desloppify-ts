interface PageLike {
  $eval<T>(selector: string, fn: (el: { textContent: string | null }) => T): Promise<T>;
}

// Puppeteer/Playwright page.$eval runs a callback in the page: it is not eval().
export async function readTitle(page: PageLike): Promise<string> {
  return page.$eval('h1', (el) => el.textContent ?? '');
}
