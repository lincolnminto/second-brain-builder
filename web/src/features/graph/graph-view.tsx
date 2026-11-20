import { lazy, Suspense, useMemo, useRef, useState, type SyntheticEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { Folder, Network, Radio, Unplug, X } from 'lucide-react';
import { useFolders } from '../../api/hooks';
import { useCurrentBrain } from '../../app/context';
import { Button } from '../../components/ui/button';
import { pageUrl } from './data';
import { countTypes, folderOptions, hasEmptyTypes, typeChips } from './filters';
import type { FocusRequest } from './graph-canvas';
import { Inspector } from './inspector';
import { menuItems, runMenuAction, type MenuAction, type MenuHandlers } from './menu';
import { NodeMenu } from './node-menu';
import { connectionCount, visibleGraph, type Scope } from './selection';
import { GraphSkeleton } from './skeleton';
import { strings } from './strings';
import { placeTooltip } from './tooltip';
import type { ColorBy } from './style';
import type { Point } from './sync';
import { useGraphQuery } from './use-graph-query';
import { useLiveGraph } from './use-live-graph';
import { useNotice } from './use-notice';
import './graph.css';

const GraphCanvas = lazy(() => import('./graph-canvas'));

type OpenMenu = { path: string; point: Point; returnFocus: HTMLElement | null };

function GraphLoading() {
  return <div className="knowledge-message" role="status"><Network size={28} strokeWidth={1.2} /><p>{strings.loading}</p></div>;
}

export function GraphView({ brainId, compact = false }: { brainId: string; compact?: boolean }) {
  const { events, connected } = useCurrentBrain();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const folder = compact ? '' : params.get('folder') ?? '';
  const query = useGraphQuery(brainId, folder);
  const graph = useLiveGraph(query.data, events, query.isFetching, brainId, !folder);
  const folders = useFolders(compact ? '' : brainId);
  const [type, setType] = useState<string | null>(null);
  const [showAllTypes, setShowAllTypes] = useState(false);
  const [colorBy, setColorBy] = useState<ColorBy>('type');
  const [selected, setSelected] = useState('');
  const [scope, setScope] = useState<Scope | null>(null);
  const [pinned, setPinned] = useState<ReadonlySet<string>>(() => new Set());
  const [menu, setMenu] = useState<OpenMenu | null>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [notice, showNotice] = useNotice();
  const [positions] = useState(() => new Map<string, Point>());
  const body = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLSelectElement>(null);
  /** Where focus goes when the inspector closes: back to where the selection was made. */
  const selectedFrom = useRef<'canvas' | 'picker'>('canvas');
  const nonce = useRef(0);

  const visible = useMemo(() => visibleGraph(graph, { type, scope }), [graph, type, scope]);
  const byPath = useMemo(() => new Map(graph.nodes.map(node => [node.path, node])), [graph]);
  const visiblePaths = useMemo(() => new Set(visible.nodes.map(node => node.path)), [visible]);
  const connections = useMemo(() => connectionCount(visible.links), [visible.links]);
  const counts = useMemo(() => countTypes(graph.nodes), [graph]);
  const chips = typeChips(counts, { showAll: showAllTypes, active: type });
  const folderList = useMemo(() => folderOptions(folders.data), [folders.data]);
  const options = useMemo(() => [...visible.nodes].sort((a, b) => (a.title || a.path).localeCompare(b.title || b.path)), [visible.nodes]);
  const selectedNode = visiblePaths.has(selected) ? byPath.get(selected) : undefined;
  const scopeNode = scope && visible.scoped ? byPath.get(scope.path) : undefined;
  const menuNode = menu ? byPath.get(menu.path) : undefined;

  const open = (path: string) => {
    const node = byPath.get(path);
    if (node) void navigate(pageUrl(brainId, node));
  };
  const focus = (path: string) => setFocusRequest({ path, nonce: ++nonce.current });
  /** Select a page from a list, showing it first when a filter hides it. */
  const reveal = (path: string) => {
    if (!visiblePaths.has(path)) {
      setType(null);
      setScope(null);
    }
    setSelected(path);
    focus(path);
  };
  const selectFromCanvas = (path: string) => {
    selectedFrom.current = 'canvas';
    setSelected(path);
    if (menu) closeMenu(false);
  };
  const closeInspector = () => {
    const canvasRegion = body.current?.querySelector<HTMLElement>('.knowledge-stage [role="region"]');
    const target = selectedFrom.current === 'picker' || !canvasRegion ? picker.current : canvasRegion;
    setSelected('');
    target?.focus();
  };
  const setFolder = (value: string) => setParams(current => {
    const next = new URLSearchParams(current);
    if (value) next.set('folder', value);
    else next.delete('folder');
    return next;
  }, { replace: true });
  const setPin = (path: string, value: boolean) => setPinned(current => {
    if (current.has(path) === value) return current;
    const next = new Set(current);
    if (value) next.add(path);
    else next.delete(path);
    return next;
  });
  const closeMenu = (restoreFocus = true) => {
    const target = menu?.returnFocus;
    setMenu(null);
    if (restoreFocus && target?.isConnected) target.focus();
  };
  const openMenu = (path: string, point: Point, returnFocus: HTMLElement | null) => setMenu({ path, point, returnFocus });
  const openMenuAtButton = (button: HTMLElement) => {
    const frame = body.current?.getBoundingClientRect();
    const rect = button.getBoundingClientRect();
    if (selectedNode && frame) openMenu(selectedNode.path, { x: rect.left - frame.left, y: rect.bottom - frame.top + 4 }, button);
  };
  const handlers: MenuHandlers = {
    brainId, origin: window.location.origin, navigate: to => void navigate(to), focus,
    showNeighbors: (path, depth) => {
      setScope({ path, depth });
      setSelected(path);
      focus(path);
    },
    setPinned: setPin,
    copy: text => navigator.clipboard.writeText(text),
  };
  async function act(action: MenuAction) {
    if (!menuNode) return;
    closeMenu(action !== 'open' && action !== 'open-source');
    const outcome = await runMenuAction(action, menuNode, handlers);
    showNotice(outcome === 'copied' ? strings.copied : outcome === 'copy-failed' ? strings.copyFailed : '');
  }

  let stage;
  if (query.isPending) stage = <GraphLoading />;
  else if (!graph.nodes.length || !visible.nodes.length) {
    const folderEmpty = !graph.nodes.length && !!folder;
    const empty = !graph.nodes.length;
    stage = <div className="knowledge-message">
      <Network size={36} strokeWidth={1} aria-hidden="true" />
      <h2>{folderEmpty ? strings.folderEmptyTitle : empty ? strings.emptyTitle : strings.filteredTitle}</h2>
      <p>{folderEmpty ? strings.folderEmptyHint : empty ? strings.emptyHint : strings.filteredHint}</p>
      {!compact && (folderEmpty
        ? <Button variant="secondary" onClick={() => setFolder('')}>{strings.clearFolder}</Button>
        : empty
          ? <Button asChild variant="secondary"><Link to={`/brains/${encodeURIComponent(brainId)}`}>{strings.addSource}</Link></Button>
          : <Button variant="secondary" onClick={() => { setType(null); setScope(null); }}>{strings.allPages}</Button>)}
    </div>;
  } else {
    stage = <Suspense fallback={<GraphSkeleton compact={compact} />}>
      <GraphCanvas graph={visible} positions={positions} selected={selectedNode?.path ?? ''} pinned={pinned} colorBy={colorBy}
        compact={compact} focusRequest={focusRequest} onOpen={open} onSelect={selectFromCanvas}
        onContextMenu={(path, point) => openMenu(path, point, document.activeElement instanceof HTMLElement ? document.activeElement : null)}
        onPinnedChange={setPin} />
    </Suspense>;
  }

  if (compact) {
    return <section className="knowledge-preview" aria-label={strings.preview}>
      {query.isError && <div className="knowledge-error" role="alert"><p>{strings.error}</p><Button variant="secondary" size="small" onClick={() => void query.refetch()}>{strings.retry}</Button></div>}
      <div className="knowledge-frame">
        <div className="knowledge-body"><div className="knowledge-stage knowledge-stage-compact">{stage}</div></div>
        <footer className="knowledge-caption">
          <span className="knowledge-counts" aria-live="polite">{strings.pages(visible.nodes.length)}<span aria-hidden="true">/</span>{strings.links(connections)}</span>
          <span className="knowledge-gesture muted">{strings.previewHint}</span>
        </footer>
      </div>
    </section>;
  }

  return <section className="page knowledge-page" aria-label={strings.title}>
    <header className="knowledge-heading">
      <div><h1>{strings.title}</h1><p className="muted">{strings.description}</p></div>
      <span className="knowledge-live" role="status">{connected ? <Radio size={14} /> : <Unplug size={14} />}{connected ? strings.live : strings.reconnecting}</span>
    </header>
    {query.isError && <div className="knowledge-error" role="alert"><p>{strings.error}</p><Button variant="secondary" size="small" onClick={() => void query.refetch()}>{strings.retry}</Button></div>}
    <div className="knowledge-frame">
      <div className="knowledge-toolbar">
        <div className="knowledge-legend" role="group" aria-label={strings.filter}>
          <button type="button" aria-pressed={type === null} onClick={() => setType(null)}>{strings.allPages}</button>
          {chips.map(chip => {
            const hintId = chip.hint ? `graph-type-hint-${chip.type}` : undefined;
            const place = chip.hint ? (event: SyntheticEvent<HTMLElement>) => placeTooltip(event.currentTarget) : undefined;
            return <span key={chip.type} className="knowledge-chip" onPointerEnter={place} onFocus={place}>
              <button type="button" aria-pressed={type === chip.type} aria-disabled={chip.disabled || undefined} aria-describedby={hintId}
                onClick={() => { if (!chip.disabled) setType(type === chip.type ? null : chip.type); }}>
                <span className={`knowledge-dot${chip.known ? ` knowledge-dot-${chip.type}` : ''}`} />
                {chip.label}<span className="knowledge-type-count">{chip.count}</span>
              </button>
              {chip.hint && <span role="tooltip" id={hintId} className="knowledge-tooltip">{chip.hint}</span>}
            </span>;
          })}
          {(showAllTypes || hasEmptyTypes(counts)) && <button type="button" className="knowledge-all-types" aria-pressed={showAllTypes}
            title={strings.allTypesHint} onClick={() => setShowAllTypes(value => !value)}>{strings.allTypes}</button>}
        </div>
        <div className="knowledge-tools">
          <span className={`knowledge-folder${folder ? ' knowledge-folder-active' : ''}`}>
            <Folder size={14} aria-hidden="true" />
            <select aria-label={strings.folder} value={folder} onChange={event => setFolder(event.target.value)}>
              <option value="">{strings.allFolders}</option>
              {folderList.map(option => <option key={option.path} value={option.path}>{`${'  '.repeat(option.depth)}${option.name}`}</option>)}
              {folder && !folderList.some(option => option.path === folder) && <option value={folder}>{folder}</option>}
            </select>
            {folder && <button type="button" className="knowledge-folder-clear" title={strings.clearFolder} aria-label={strings.clearFolder} onClick={() => setFolder('')}><X size={13} /></button>}
          </span>
          <span className="knowledge-segmented" role="group" aria-label={strings.colorBy}>
            <span className="knowledge-segmented-label" aria-hidden="true">{strings.colorBy}</span>
            <button type="button" aria-pressed={colorBy === 'type'} onClick={() => setColorBy('type')}>{strings.colorByType}</button>
            <button type="button" aria-pressed={colorBy === 'community'} onClick={() => setColorBy('community')}>{strings.colorByCommunity}</button>
          </span>
        </div>
      </div>
      {scope && scopeNode && <div className="knowledge-scope">
        <span>{strings.scope(scopeNode.title || scopeNode.path, scope.depth)}</span>
        <Button variant="secondary" size="small" onClick={() => setScope(null)}>{strings.showAll}</Button>
      </div>}
      <div ref={body} className={`knowledge-body${selectedNode ? ' knowledge-body-inspecting' : ''}`}>
        <div className="knowledge-stage">{stage}</div>
        {selectedNode && <Inspector brainId={brainId} node={selectedNode} model={graph} pinned={pinned.has(selectedNode.path)}
          onSelect={reveal} onClose={closeInspector} onActions={openMenuAtButton} />}
        {menu && menuNode && <NodeMenu title={menuNode.title || menuNode.path} items={menuItems(brainId, menuNode, pinned.has(menuNode.path))}
          point={menu.point} onAction={action => void act(action)} onClose={() => closeMenu()} />}
      </div>
      <footer className="knowledge-caption">
        <span className="knowledge-counts" aria-live="polite">
          {strings.pages(visible.nodes.length)}<span aria-hidden="true">/</span>{strings.links(connections)}
          {query.isPlaceholderData && <span className="muted">{strings.updating}</span>}
        </span>
        <span className="knowledge-notice" role="status">{notice && <span key={notice.id}>{notice.text}</span>}</span>
        <span className="knowledge-gesture muted">{strings.hint}</span>
      </footer>
    </div>
    {graph.nodes.length > 0 && <div className="knowledge-explorer">
      <div className="knowledge-picker">
        <label htmlFor="graph-page-picker">{strings.picker}</label>
        <select ref={picker} id="graph-page-picker" className="input select" value={selectedNode?.path ?? ''} onChange={event => {
          selectedFrom.current = 'picker';
          if (event.target.value) reveal(event.target.value);
          else setSelected('');
        }}>
          <option value="">{strings.pickerPlaceholder}</option>
          {options.map(node => <option key={node.path} value={node.path}>{node.title || node.path}</option>)}
        </select>
      </div>
    </div>}
    <p id="graph-keyboard-hint" className="sr-only">{strings.keyboardHint}</p>
  </section>;
}
