import { fileUrl, type Schema } from '../../api/client';
import { passageHash } from '../../components/document-viewer/passage-hash';

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

/** The Wiki Page types, in reading order. */
export const WIKI_PAGE_TYPES = ['summary', 'entity', 'concept', 'comparison', 'synthesis', 'name-index'] as const;
export type WikiPageType = typeof WIKI_PAGE_TYPES[number];
export function isWikiPageType(type: string): type is WikiPageType {
  return (WIKI_PAGE_TYPES as readonly string[]).includes(type);
}

export function groupWikiPages(pages: Schema['PageSummaryView'][]) {
  const order: readonly string[] = WIKI_PAGE_TYPES;
  const groups = new Map<string, Schema['PageSummaryView'][]>();
  for (const page of pages) {
    if (!page.type || page.type === 'source') continue;
    groups.set(page.type, [...(groups.get(page.type) ?? []), page]);
  }
  return [...groups].sort(([a], [b]) => (order.includes(a) ? order.indexOf(a) : order.length) -
    (order.includes(b) ? order.indexOf(b) : order.length) || a.localeCompare(b))
    .map(([type, entries]) => ({ type, pages: entries.sort((a, b) => a.title.localeCompare(b.title)) }));
}

/** The slug of the Source whose Canonical Document is at `path` (`sources/<folder>/<slug>/<slug>.md`). */
export function sourceSlug(path: string): string | null {
  const parts = path.replace(/^\/+/, '').split('/');
  const file = parts.at(-1);
  const directory = parts.at(-2);
  return parts.length >= 3 && parts[0] === 'sources' && directory && file === `${directory}.md` ? directory : null;
}

/** The Source view, at the passage (`#passage=<char_start>-<char_end>`, read by its DocumentViewer) when one is given. */
export function sourceViewUrl(brainId: string, slug: string, passage?: { char_start: number; char_end: number }) {
  const base = `/brains/${encodeURIComponent(brainId)}/sources/${encodeURIComponent(slug)}`;
  return passage ? base + passageHash(passage) : base;
}

/** The Source view of the passage that a citation resolves to, or null when its path is not a Canonical Document. */
export function passageUrl(brainId: string, passage: Schema['CitationPassage']) {
  const slug = sourceSlug(passage.source_path);
  return slug ? sourceViewUrl(brainId, slug, passage) : null;
}

/** The Source view of a cited Canonical Document (a `sources[].resource`), or null when it is not one. */
export function citedSourceUrl(resource: string, pagePath: string, brainId: string) {
  const url = localUrl(resource, pagePath);
  if (!url || url.origin !== origin || resource.startsWith('//')) return null;
  try {
    const slug = sourceSlug(url.pathname.slice(1).split('/').map(decodeURIComponent).join('/'));
    return slug ? sourceViewUrl(brainId, slug) : null;
  } catch { return null; }
}
