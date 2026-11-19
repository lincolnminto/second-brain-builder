export function resolveBrainPath(href: string, pagePath: string): string | null {
  try {
    const base = new URL(pagePath.replace(/^\//, ''), 'https://brain.invalid/');
    const url = new URL(href, base);
    if (url.origin !== base.origin) return null;
    return url.pathname.slice(1) + url.search + url.hash;
  } catch { return null; }
}
