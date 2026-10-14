declare global {
  interface ImportMeta {
    readonly env: Record<string, string | undefined>;
  }
}

export {};
