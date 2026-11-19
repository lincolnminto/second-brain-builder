import { lazy, Suspense, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { ArrowUpRight, Network, Radio, Unplug } from 'lucide-react';
import { useGraph } from '../../api/hooks';
import { useCurrentBrain } from '../../app/context';
import { Button } from '../../components/ui/button';
import { filterGraph, wikiPageUrl } from './data';
import { strings } from './strings';
import { useLiveGraph } from './use-live-graph';
import './graph.css';

const GraphCanvas = lazy(() => import('./graph-canvas'));

function GraphLoading() {
  return <div className="knowledge-message" role="status"><Network size={28} strokeWidth={1.2} /><p>{strings.loading}</p></div>;
}

function GraphView({ brainId, compact = false }: { brainId: string; compact?: boolean }) {
  const { events, connected } = useCurrentBrain();
  const query = useGraph(brainId);
  const graph = useLiveGraph(query.data, events, query.isFetching, brainId);
  const [type, setType] = useState<string | null>(null);
  const [selected, setSelected] = useState('');
  const visible = useMemo(() => filterGraph(graph, type), [graph, type]);
  const counts = useMemo(() => {
    const result = new Map<string, number>();
    for (const node of graph.nodes) result.set(node.type, (result.get(node.type) ?? 0) + 1);
    return result;
  }, [graph]);
  const types = [...Object.keys(strings.pageTypes), ...[...counts.keys()].filter(key => !Object.hasOwn(strings.pageTypes, key)).sort()];
  const options = useMemo(() => [...visible.nodes].sort((a, b) => a.title.localeCompare(b.title)), [visible.nodes]);
  const selectedNode = visible.nodes.find(node => node.path === selected);
  const pageTypeName = (key: string) => Object.hasOwn(strings.pageTypes, key) ? strings.pageTypes[key as keyof typeof strings.pageTypes] : strings.unknown;
  const isKnownType = (key: string) => Object.hasOwn(strings.pageTypes, key);

  return <section className={compact ? 'knowledge-preview' : 'page knowledge-page'} aria-label={compact ? strings.preview : strings.title}>
    {!compact && <header className="knowledge-heading">
      <div><h1>{strings.title}</h1><p className="muted">{strings.description}</p></div>
      <span className="knowledge-live" role="status">{connected ? <Radio size={14} /> : <Unplug size={14} />}{connected ? strings.live : strings.reconnecting}</span>
    </header>}
    {query.isError && <div className="knowledge-error" role="alert"><p>{strings.error}</p><Button variant="secondary" size="small" onClick={() => void query.refetch()}>{strings.retry}</Button></div>}
    <div className="knowledge-frame">
      {!compact && <div className="knowledge-toolbar">
        <div className="knowledge-legend" role="group" aria-label={strings.filter}>
          <button type="button" aria-pressed={type === null} onClick={() => { setType(null); setSelected(''); }}>{strings.all}</button>
          {types.map(key => <button key={key} type="button" aria-pressed={type === key} onClick={() => { setType(type === key ? null : key); setSelected(''); }}>
            <span className={`knowledge-dot${isKnownType(key) ? ` knowledge-dot-${key}` : ''}`} />
            {pageTypeName(key)}<span className="knowledge-type-count">{counts.get(key) ?? 0}</span>
          </button>)}
        </div>
      </div>}
      <div className={`knowledge-stage${compact ? ' knowledge-stage-compact' : ''}`}>
        {query.isPending ? <GraphLoading /> : !visible.nodes.length ? <div className="knowledge-message">
          <Network size={36} strokeWidth={1} aria-hidden="true" />
          <h2>{type === null ? strings.emptyTitle : strings.filteredTitle}</h2>
          <p>{type === null ? strings.emptyHint : strings.filteredHint}</p>
          {!compact && (type === null
            ? <Button asChild variant="secondary"><Link to={`/brains/${encodeURIComponent(brainId)}`}>{strings.addSource}</Link></Button>
            : <Button variant="secondary" onClick={() => setType(null)}>{strings.all}</Button>)}
        </div> : <Suspense fallback={<GraphLoading />}><GraphCanvas brainId={brainId} graph={visible} neighbors={graph.neighbors} focusPath={selectedNode?.path ?? ''} compact={compact} /></Suspense>}
      </div>
      <footer className="knowledge-caption">
        <span className="knowledge-counts" aria-live="polite">{strings.pages(visible.nodes.length)}<span aria-hidden="true">/</span>{strings.connections(visible.links.length)}</span>
        {!compact && <span className="knowledge-gesture muted">{strings.hint}</span>}
      </footer>
    </div>
    {!compact && graph.nodes.length > 0 && <div className="knowledge-explorer">
      <div className="knowledge-picker">
        <label htmlFor="graph-page-picker">{strings.picker}</label>
        <select id="graph-page-picker" className="input select" value={selectedNode?.path ?? ''} onChange={event => setSelected(event.target.value)}>
          <option value="">{strings.pickerPlaceholder}</option>
          {options.map(node => <option key={node.path} value={node.path}>{node.title || node.path}</option>)}
        </select>
      </div>
      {selectedNode && <Button asChild variant="secondary"><Link to={wikiPageUrl(brainId, selectedNode.path)}>{strings.openPage}<ArrowUpRight size={15} /></Link></Button>}
      <p className="sr-only">{strings.keyboardHint}</p>
    </div>}
  </section>;
}

export function GraphPlaceholder() {
  const { brainId } = useCurrentBrain();
  return <GraphView key={brainId} brainId={brainId} />;
}

/** A live, 280px-high graph with no legend, picker, or zoom controls.
 * Defaults to the current Brain; an explicit id can be used on Brain home. */
export function GraphPreview({ brainId }: { brainId?: string } = {}) {
  const current = useCurrentBrain();
  const id = brainId ?? current.brainId;
  return <GraphView key={id} brainId={id} compact />;
}
