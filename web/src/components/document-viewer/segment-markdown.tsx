import { memo, useMemo } from 'react';
import Markdown, { type Components, type Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link } from 'react-router';
import { fileUrl } from '../../api/client';
import { resolveBrainPath } from '../markdown-path';
import { remarkKeepOrphanFootnotes } from './footnotes';
import { rehypeHighlightRanges, type HighlightRange } from './highlight';

type PluggableList = NonNullable<Options['rehypePlugins']>;

const REMARK_PLUGINS: PluggableList = [remarkGfm];
const SEGMENT_REMARK_PLUGINS: PluggableList = [remarkGfm, remarkKeepOrphanFootnotes];
const NO_PLUGINS: PluggableList = [];
/** The id prefix that react-markdown uses by default, as in `SafeMarkdown`. */
export const DEFAULT_ID_PREFIX = 'user-content-';

// The same links and images as SafeMarkdown: Brain paths stay in the app.
function brainComponents(brainId: string, pagePath: string): Components {
  return {
    a: ({ href = '', children, id, className, 'aria-label': label, 'aria-describedby': describedBy }) => {
      const path = resolveBrainPath(href, pagePath);
      // In-page links keep their id and labels: footnote references and back references point at each other.
      if (href.startsWith('#')) return <a href={href} id={id} className={className} aria-label={label} aria-describedby={describedBy}>{children}</a>;
      if (path) return <Link to={`/brains/${encodeURIComponent(brainId)}/wiki/${path}`}>{children}</Link>;
      return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
    img: ({ src = '', alt = '' }) => {
      const path = resolveBrainPath(src, pagePath);
      return path ? <img src={fileUrl(brainId, path)} alt={alt} loading="lazy" /> : <span>{alt}</span>;
    },
  };
}

/**
 * Untrusted Markdown of a page or of one segment, without raw HTML, with `ranges`
 * of the source string wrapped in `<mark>` elements.
 *
 * `segment` is for one segment of a long page, parsed alone: footnote definitions
 * whose reference is in another segment stay in the text. `idPrefix` keeps the
 * footnote ids of each segment apart.
 */
export const SegmentMarkdown = memo(function SegmentMarkdown({ markdown, ranges, brainId, pagePath, segment = false, idPrefix = DEFAULT_ID_PREFIX }: {
  markdown: string;
  ranges: readonly HighlightRange[];
  brainId: string;
  pagePath: string;
  segment?: boolean;
  idPrefix?: string;
}) {
  const components = useMemo(() => brainComponents(brainId, pagePath), [brainId, pagePath]);
  const rehypePlugins = useMemo<PluggableList>(
    () => (ranges.length ? [[rehypeHighlightRanges, { source: markdown, ranges }]] : NO_PLUGINS),
    [markdown, ranges],
  );
  const remarkRehypeOptions = useMemo(() => ({ clobberPrefix: idPrefix }), [idPrefix]);
  return <Markdown remarkPlugins={segment ? SEGMENT_REMARK_PLUGINS : REMARK_PLUGINS} rehypePlugins={rehypePlugins}
    remarkRehypeOptions={remarkRehypeOptions} skipHtml components={components}>{markdown}</Markdown>;
});
