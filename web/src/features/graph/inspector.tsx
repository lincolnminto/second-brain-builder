import { useEffect, useMemo, useRef, type KeyboardEvent } from 'react';
import { Link } from 'react-router';
import { ArrowUpRight, MoreHorizontal, Pin, X } from 'lucide-react';
import { usePage } from '../../api/hooks';
import { Button } from '../../components/ui/button';
import { opensSourceView, pageUrl, type GraphModel, type GraphNode } from './data';
import { isPageType, typeLabel } from './filters';
import { pageLinks } from './selection';
import { strings } from './strings';
import { usePageSummaries } from './use-graph-query';

type Props = {
  brainId: string;
  node: GraphNode;
  model: Pick<GraphModel, 'nodes' | 'links'>;
  pinned: boolean;
  onSelect: (path: string) => void;
  onClose: () => void;
  onActions: (button: HTMLElement) => void;
};

function TypeDot({ type }: { type: string }) {
  return <span className={`knowledge-dot${isPageType(type) ? ` knowledge-dot-${type}` : ''}`} aria-hidden="true" />;
}

function LinkList({ title, nodes, onSelect }: { title: string; nodes: GraphNode[]; onSelect: (path: string) => void }) {
  return <section className="graph-inspector-links">
    <h3>{title}<span className="knowledge-type-count">{nodes.length}</span></h3>
    {nodes.length ? <ul>{nodes.map(node => <li key={node.path}>
      <button type="button" onClick={() => onSelect(node.path)}><TypeDot type={node.type} /><span>{node.title || node.path}</span></button>
    </li>)}</ul> : <p className="muted">{strings.noLinks}</p>}
  </section>;
}

/** Details of the selected page. A Canonical Document can be very large, so
 * its description comes from the page summaries instead of the full page, and
 * it opens in its Source view. Following a link moves focus to the heading of
 * the newly selected page, because the activated link leaves the lists. */
export function Inspector({ brainId, node, model, pinned, onSelect, onClose, onActions }: Props) {
  const canonical = node.type === 'source';
  const heading = useRef<HTMLHeadingElement>(null);
  const followed = useRef(false);
  useEffect(() => {
    if (!followed.current) return;
    followed.current = false;
    heading.current?.focus();
  }, [node.path]);
  const follow = (path: string) => {
    followed.current = path !== node.path;
    onSelect(path);
  };
  const page = usePage(brainId, canonical ? '' : node.path);
  const summaries = usePageSummaries(brainId, 'source', canonical);
  const description = canonical ? summaries.data?.find(summary => summary.path === node.path)?.description : page.data?.description;
  const tags = canonical ? [] : page.data?.tags ?? [];
  const links = useMemo(() => pageLinks(model, node.path), [model, node.path]);
  const keyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    onClose();
  };

  return <aside className="graph-inspector" aria-label={strings.inspector} onKeyDown={keyboard}>
    <header>
      <span className="graph-inspector-type"><TypeDot type={node.type} />{typeLabel(node.type)}
        {pinned && <span className="graph-inspector-pinned"><Pin size={12} aria-hidden="true" />{strings.pinned}</span>}
      </span>
      <Button variant="ghost" size="icon" title={strings.close} aria-label={strings.close} onClick={onClose}><X size={16} /></Button>
    </header>
    <h2 ref={heading} tabIndex={-1}>{node.title || node.path}</h2>
    {description && <p className="graph-inspector-description">{description}</p>}
    {!!tags.length && <ul className="graph-inspector-tags" aria-label={strings.tags}>{tags.map(tag => <li key={tag}>{tag}</li>)}</ul>}
    <p className="graph-inspector-degree">{strings.connections(node.degree)}</p>
    <LinkList title={strings.linksOut} nodes={links.outgoing} onSelect={follow} />
    <LinkList title={strings.linksIn} nodes={links.incoming} onSelect={follow} />
    <footer>
      <Button asChild variant="secondary" size="small"><Link to={pageUrl(brainId, node)}>{opensSourceView(brainId, node) ? strings.openSource : strings.openPage}<ArrowUpRight size={14} /></Link></Button>
      <Button variant="ghost" size="small" aria-haspopup="menu" onClick={event => onActions(event.currentTarget)}><MoreHorizontal size={16} />{strings.actions}</Button>
    </footer>
  </aside>;
}
