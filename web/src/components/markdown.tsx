import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link } from 'react-router';
import { fileUrl } from '../api/client';
import { stripLeadingDuplicateH1 } from './markdown-body';
import { resolveBrainPath } from './markdown-path';

export function SafeMarkdown({ body, brainId, pagePath = 'index.md', title }: { body: string; brainId: string; pagePath?: string; title?: string }) {
  const markdown = title ? stripLeadingDuplicateH1(body, title) : body;
  return <div className="reading"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
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
  }}>{markdown}</Markdown></div>;
}
