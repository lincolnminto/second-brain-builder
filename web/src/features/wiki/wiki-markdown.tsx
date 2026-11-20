import { useEffect, useMemo } from 'react';
import Markdown, { type Components, type Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link, useLocation } from 'react-router';
import type { Schema } from '../../api/client';
import { stripLeadingDuplicateH1 } from '../../components/markdown-body';
import { CitationReference } from './citation-reference';
import { citationRefs } from './citations';
import { headingIds } from './headings';
import { citedSourceUrl, resolveCitation, resolveWikiImage, resolveWikiLink } from './resolution';
import { t } from './strings';
import { urlFootnoteReferences } from './url-references';

const REMARK_PLUGINS: Options['remarkPlugins'] = [remarkGfm, urlFootnoteReferences, headingIds];
const REHYPE_PLUGINS: Options['rehypePlugins'] = [citationRefs];
const REMARK_REHYPE_OPTIONS: Options['remarkRehypeOptions'] = { footnoteLabel: t('footnotes'), footnoteBackLabel: t('backReference') };

/** The renderers of a page's links and images. A new renderer is a new element type,
 * and React would remount every reference: build them once per page. */
function pageComponents(brainId: string, pagePath: string, sources: Schema['SourceRefView'][]): Components {
  const internalLink = (resource: string) => {
    const target = resolveWikiLink(resource, pagePath, brainId);
    return target.kind === 'internal' ? target.to : undefined;
  };
  return {
    a: ({ href = '', children, node, ...props }) => {
      const reference = node?.properties.dataCitationRef;
      if (typeof reference === 'number') {
        const citation = resolveCitation(href, sources);
        const documentTo = citation && (citedSourceUrl(citation.resource, pagePath, brainId) ?? internalLink(citation.resource));
        if (citation && documentTo) {
          const sentence = node?.properties.dataCitedSentence;
          return <CitationReference brainId={brainId} pagePath={pagePath} refIndex={reference} sentence={typeof sentence === 'string' ? sentence : ''}
            sourceId={citation.id} title={citation.title || citation.id} documentTo={documentTo} id={props.id}>{children}</CitationReference>;
        }
        // A footnote that cites no known Source stays a plain link to its note.
        return <a href={href} id={props.id} aria-describedby={props['aria-describedby']} data-footnote-ref="">{children}</a>;
      }
      const target = resolveWikiLink(href, pagePath, brainId);
      if (target.kind === 'blocked') return <span>{children}</span>;
      if (target.kind === 'internal') return <Link {...props} to={target.to}>{children}</Link>;
      if (target.kind === 'external') return <a {...props} href={target.to} target="_blank" rel="noopener noreferrer">{children}</a>;
      return <a {...props} href={target.to}>{children}</a>;
    },
    img: ({ src = '', alt = '' }) => {
      const local = typeof src === 'string' ? resolveWikiImage(src, pagePath, brainId) : null;
      return local ? <img src={local} alt={alt} loading="lazy" /> : <span>{alt}</span>;
    },
  };
}

export function WikiMarkdown({ brainId, page }: { brainId: string; page: Schema['PageView'] }) {
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    try { document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.({ block: 'start' }); } catch { /* A malformed fragment must not prevent reading. */ }
  }, [hash, page.path, page.body]);
  const body = stripLeadingDuplicateH1(page.body, page.title);
  const { path, sources } = page;
  // The Wiki Page view renders again on each job event. The same element keeps the
  // references mounted, so an open citation card and the focus in it stay.
  const content = useMemo(() => <Markdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} skipHtml
    remarkRehypeOptions={REMARK_REHYPE_OPTIONS} components={pageComponents(brainId, path, sources)}>{body}</Markdown>, [body, brainId, path, sources]);
  return <div className="reading wiki-reading">{content}</div>;
}
