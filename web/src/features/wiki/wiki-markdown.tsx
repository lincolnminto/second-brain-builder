import { useEffect, useId, useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link, useLocation } from 'react-router';
import type { Schema } from '../../api/client';
import { stripLeadingDuplicateH1 } from '../../components/markdown-body';
import { headingIds } from './headings';
import { resolveCitation, resolveWikiImage, resolveWikiLink } from './resolution';
import { t } from './strings';

function CitationLink({ to, title, children, id }: { to: string; title: string; children: ReactNode; id?: string }) {
  const tooltipId = useId();
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  function show(element: HTMLAnchorElement) {
    const rect = element.getBoundingClientRect();
    setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - 288)), top: rect.bottom + 112 < window.innerHeight ? rect.bottom + 8 : Math.max(16, rect.top - 104) });
  }
  return <Link to={to} id={id} className="wiki-citation" aria-label={t('citation', { title })} aria-describedby={position ? tooltipId : undefined}
    onMouseEnter={event => show(event.currentTarget)} onMouseLeave={() => setPosition(null)}
    onFocus={event => show(event.currentTarget)} onBlur={() => setPosition(null)} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setPosition(null); } }}>
    {children}{position && <span role="tooltip" id={tooltipId} className="wiki-citation-card" style={position}><strong>{title}</strong><span>{t('canonical')}</span></span>}
  </Link>;
}

export function WikiMarkdown({ brainId, page }: { brainId: string; page: Schema['PageView'] }) {
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    try { document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.({ block: 'start' }); } catch { /* A malformed fragment must not prevent reading. */ }
  }, [hash, page.path, page.body]);
  const body = stripLeadingDuplicateH1(page.body, page.title);
  return <div className="reading wiki-reading"><Markdown remarkPlugins={[remarkGfm, headingIds]} skipHtml
    remarkRehypeOptions={{ footnoteLabel: t('footnotes'), footnoteBackLabel: t('backReference') }} components={{
      a: ({ href = '', children, node, ...props }) => {
        const citation = node?.properties.dataFootnoteRef ? resolveCitation(href, page.sources) : undefined;
        if (citation) {
          const target = resolveWikiLink(citation.resource, page.path, brainId);
          if (target.kind === 'internal') return <CitationLink to={target.to} title={citation.title || citation.id} id={props.id}>{children}</CitationLink>;
        }
        const target = resolveWikiLink(href, page.path, brainId);
        if (target.kind === 'blocked') return <span>{children}</span>;
        if (target.kind === 'internal') return <Link {...props} to={target.to}>{children}</Link>;
        if (target.kind === 'external') return <a {...props} href={target.to} target="_blank" rel="noopener noreferrer">{children}</a>;
        return <a {...props} href={target.to}>{children}</a>;
      },
      img: ({ src = '', alt = '' }) => {
        const local = typeof src === 'string' ? resolveWikiImage(src, page.path, brainId) : null;
        return local ? <img src={local} alt={alt} loading="lazy" /> : <span>{alt}</span>;
      },
    }}>{body}</Markdown></div>;
}
