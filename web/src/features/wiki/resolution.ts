import { fileUrl, type Schema } from '../../api/client';

type ResolvedLink = { kind: 'internal' | 'anchor' | 'external' | 'asset'; to: string } | { kind: 'blocked' };
const origin = 'https://brain.invalid';

function localUrl(href: string, pagePath: string) {
  if (!href.trim() || href.includes('\\')) return null;
  try { return new URL(href, new URL(pagePath.replace(/^\//, ''), `${origin}/`)); } catch { return null; }
}

export function resolveWikiLink(href: string, pagePath: string, brainId: string): ResolvedLink {
  const url = localUrl(href, pagePath);
  if (!url) return { kind: 'blocked' };
  if (href.startsWith('#')) return { kind: 'anchor', to: href };
  if (url.origin !== origin || href.startsWith('//')) {
    return ['https:', 'http:', 'mailto:'].includes(url.protocol) ? { kind: 'external', to: href } : { kind: 'blocked' };
  }
  try {
    const path = url.pathname.slice(1).split('/').map(decodeURIComponent).join('/');
    const suffix = url.search + url.hash;
    if (!path.endsWith('.md')) return { kind: 'asset', to: fileUrl(brainId, path) + suffix };
    const encoded = path.split('/').map(encodeURIComponent).join('/');
    return { kind: 'internal', to: `/brains/${encodeURIComponent(brainId)}/wiki/${encoded}${suffix}` };
  } catch { return { kind: 'blocked' }; }
}

export function resolveWikiImage(src: string, pagePath: string, brainId: string): string | null {
  const url = localUrl(src, pagePath);
  if (!url || url.origin !== origin || src.startsWith('//')) return null;
  try { return fileUrl(brainId, url.pathname.slice(1).split('/').map(decodeURIComponent).join('/')) + url.search; } catch { return null; }
}

export function resolveCitation(href: string, sources: Schema['SourceRefView'][]) {
  const prefix = '#user-content-fn-';
  if (!href.startsWith(prefix)) return undefined;
  try {
    const id = decodeURIComponent(href.slice(prefix.length));
    return sources.find(source => source.id.toLowerCase() === id.toLowerCase());
  } catch { return undefined; }
}

export function groupWikiPages(pages: Schema['PageSummaryView'][]) {
  const order = ['summary', 'entity', 'concept', 'comparison', 'synthesis', 'name-index'];
  const groups = new Map<string, Schema['PageSummaryView'][]>();
  for (const page of pages) {
    if (!page.type || page.type === 'source') continue;
    groups.set(page.type, [...(groups.get(page.type) ?? []), page]);
  }
  return [...groups].sort(([a], [b]) => (order.includes(a) ? order.indexOf(a) : order.length) -
    (order.includes(b) ? order.indexOf(b) : order.length) || a.localeCompare(b))
    .map(([type, entries]) => ({ type, pages: entries.sort((a, b) => a.title.localeCompare(b.title)) }));
}
