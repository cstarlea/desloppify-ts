import type { MDXComponents } from 'mdx/types';

// Required by @next/mdx; loaded by the framework, never imported.
export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    h1: ({ children }) => <h1 className="title">{children}</h1>,
    p: ({ children }) => <p className="body">{children}</p>,
    a: ({ href, children }) => <a href={href}>{children}</a>,
    ...components,
  };
}
