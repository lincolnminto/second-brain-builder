import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { UndirectedGraph } from 'graphology';
import { Maximize2, Minus, Plus } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { communityIndexes } from './communities';
import type { GraphSubset } from './data';
import { GraphRenderer, type RendererCallbacks } from './renderer';
import { connectionCount } from './selection';
import { strings } from './strings';
import type { ColorBy } from './style';
import { rememberPositions, syncGraph, type Point, type RenderEdge, type RenderNode } from './sync';
import { useGraphPalette, useReducedMotion } from './use-canvas';

/** Ask the canvas to frame a page and its neighbors once (a new nonce asks again). */
export type FocusRequest = { path: string; nonce: number };

type Props = {
  graph: GraphSubset;
  /** Last positions by path. The owner keeps it, so positions survive filters and remounts. */
  positions: Map<string, Point>;
  selected?: string;
  pinned?: ReadonlySet<string>;
  colorBy?: ColorBy;
  compact?: boolean;
  focusRequest?: FocusRequest | null;
  onOpen: (path: string) => void;
  onSelect?: (path: string) => void;
  onContextMenu?: (path: string, point: Point) => void;
  onPinnedChange?: (path: string, pinned: boolean) => void;
};

const NO_PINS: ReadonlySet<string> = new Set();
const noop = () => undefined;

export default function GraphCanvas({
  graph, positions, selected = '', pinned = NO_PINS, colorBy = 'type', compact = false, focusRequest = null,
  onOpen, onSelect = noop, onContextMenu = noop, onPinnedChange = noop,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const palette = useGraphPalette();
  const reduced = useReducedMotion();
  const [render] = useState(() => new UndirectedGraph<RenderNode, RenderEdge>());
  const [failed, setFailed] = useState(false);
  const renderer = useRef<GraphRenderer | null>(null);
  const settled = useRef(false);
  const lastPins = useRef<ReadonlySet<string>>(NO_PINS);
  const handledFocus = useRef(-1);
  const callbacks = useRef<RendererCallbacks>({ select: onSelect, open: onOpen, contextMenu: onContextMenu, pin: path => onPinnedChange(path, true) });
  useEffect(() => {
    callbacks.current = { select: onSelect, open: onOpen, contextMenu: onContextMenu, pin: path => onPinnedChange(path, true) };
  });
  const initialMotion = useRef(reduced);

  useEffect(() => {
    let instance: GraphRenderer;
    try {
      instance = new GraphRenderer(container.current!, render, {
        compact, reducedMotion: initialMotion.current, callbacks: () => callbacks.current,
        onLayoutSettled: () => { settled.current = true; },
      });
    } catch {
      setFailed(true);
      return;
    }
    renderer.current = instance;
    // A remount (or React's development double mount) resumes the layout.
    if (render.order) instance.layout.restart(settled.current);
    return () => {
      instance.layout.flush();
      rememberPositions(render, positions);
      instance.kill();
      renderer.current = null;
    };
  }, [render, compact, positions]);

  useEffect(() => {
    // New pages start next to their linked pages, so first write the positions of a layout in flight.
    renderer.current?.layout.flush();
    const result = syncGraph(render, graph, { positions, pinned, compact, hubs: compact ? 5 : 10 });
    const pinnedChanged = pinned !== lastPins.current;
    lastPins.current = pinned;
    renderer.current?.synced(result, pinnedChanged);
  }, [render, graph, pinned, compact, positions]);

  const connections = useMemo(() => connectionCount(graph.links), [graph.links]);
  // Every page's last community color, so colors stay put across live updates and filters.
  const colorMemory = useRef(new Map<string, number>());
  const communities = useMemo(() => {
    if (colorBy !== 'community') return new Map<string, number>();
    const result = communityIndexes(graph, colorMemory.current);
    for (const [path, color] of result) if (color >= 0) colorMemory.current.set(path, color);
    return result;
  }, [colorBy, graph]);
  useEffect(() => {
    renderer.current?.update({ palette, colorBy, communities, selected: selected || null, reducedMotion: reduced });
  }, [palette, colorBy, communities, selected, reduced]);

  useEffect(() => {
    if (!focusRequest || handledFocus.current === focusRequest.nonce || !renderer.current || !render.hasNode(focusRequest.path)) return;
    handledFocus.current = focusRequest.nonce;
    renderer.current.focus(focusRequest.path);
  }, [focusRequest, graph, render]);

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    const instance = renderer.current;
    if (event.target !== event.currentTarget || !instance) return;
    const contextMenu = event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey);
    switch (true) {
      case event.key === '+' || event.key === '=': instance.zoom(1.3); break;
      case event.key === '-': instance.zoom(1 / 1.3); break;
      case event.key === '0': instance.fitAll(); break;
      case event.key === 'ArrowLeft': instance.pan(-0.15, 0); break;
      case event.key === 'ArrowRight': instance.pan(0.15, 0); break;
      case event.key === 'ArrowUp': instance.pan(0, -0.15); break;
      case event.key === 'ArrowDown': instance.pan(0, 0.15); break;
      case event.key === 'Escape' && !!selected: onSelect(''); break;
      case event.key === 'Enter' && !!selected: onOpen(selected); break;
      case contextMenu && !!selected && !compact: {
        const point = instance.nodePoint(selected);
        if (point) onContextMenu(selected, point);
        break;
      }
      default: return;
    }
    event.preventDefault();
  }

  if (failed) return <div className="knowledge-message" role="alert"><p>{strings.webglError}</p></div>;
  return <div className={`knowledge-canvas${compact ? ' knowledge-canvas-compact' : ''}`}>
    <div ref={container} className="knowledge-sigma" role="region" tabIndex={0} onKeyDown={keyboard}
      aria-label={strings.canvasSummary(graph.nodes.length, connections)} aria-describedby={compact ? undefined : 'graph-keyboard-hint'} />
    {!compact && <div className="knowledge-canvas-controls">
      <Button variant="secondary" size="icon" title={strings.zoomOut} aria-label={strings.zoomOut} onClick={() => renderer.current?.zoom(1 / 1.4)}><Minus size={17} /></Button>
      <Button variant="secondary" size="icon" title={strings.zoomIn} aria-label={strings.zoomIn} onClick={() => renderer.current?.zoom(1.4)}><Plus size={17} /></Button>
      <Button variant="secondary" size="icon" title={strings.fit} aria-label={strings.fit} onClick={() => renderer.current?.fitAll()}><Maximize2 size={17} /></Button>
    </div>}
  </div>;
}
