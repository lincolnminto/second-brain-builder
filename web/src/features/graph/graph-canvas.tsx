import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d';
import { Maximize2, Minus, Plus } from 'lucide-react';
import { useNavigate } from 'react-router';
import { Button } from '../../components/ui/button';
import { wikiPageUrl, type GraphModel, type GraphNode } from './data';
import { strings } from './strings';
import { useCanvasSize, useGraphPalette, useReducedMotion } from './use-canvas';
import { shouldAutoFit } from './autofit';

type Props = {
  brainId: string;
  graph: Pick<GraphModel, 'nodes' | 'links'>;
  neighbors: GraphModel['neighbors'];
  focusPath: string;
  compact: boolean;
};

export default function GraphCanvas({ brainId, graph, neighbors, focusPath, compact }: Props) {
  const { container, width, height } = useCanvasSize();
  const palette = useGraphPalette();
  const reduced = useReducedMotion();
  const navigate = useNavigate();
  const engine = useRef<ForceGraphMethods<GraphNode> | undefined>(undefined);
  const manuallyMoved = useRef(false);
  const previousPaths = useRef<ReadonlySet<string>>(new Set());
  const needsFit = useRef(false);
  const fitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focused = useRef('');
  const frameTime = useRef(0);
  const [hovered, setHovered] = useState<string | null>(null);
  const activePath = hovered ?? (graph.nodes.some(node => node.path === focusPath) ? focusPath : null);
  const highlighted = useMemo(() => new Set(activePath ? [activePath, ...(neighbors.get(activePath) ?? [])] : []), [activePath, neighbors]);
  const duration = reduced ? 0 : 200;

  const fit = useCallback(() => engine.current?.zoomToFit(duration, compact ? 28 : 56), [duration, compact]);
  const scheduleFit = useCallback(() => {
    clearTimeout(fitTimer.current);
    fitTimer.current = setTimeout(() => { if (!manuallyMoved.current) fit(); }, 350);
  }, [fit]);
  function manualMove() {
    manuallyMoved.current = true;
    clearTimeout(fitTimer.current);
  }
  useEffect(() => {
    const paths = graph.nodes.map(node => node.path);
    if (shouldAutoFit(manuallyMoved.current, previousPaths.current, paths)) {
      needsFit.current = true;
      scheduleFit();
    }
    previousPaths.current = new Set(paths);
  }, [graph.nodes, scheduleFit]);
  useEffect(() => () => clearTimeout(fitTimer.current), []);
  useEffect(() => {
    if (!focusPath) { focused.current = ''; return; }
    if (focused.current === focusPath) return;
    const node = graph.nodes.find(item => item.path === focusPath);
    if (node && Number.isFinite(node.x) && Number.isFinite(node.y) && engine.current) {
      focused.current = focusPath;
      manualMove();
      engine.current.centerAt(node.x, node.y, duration);
      engine.current.zoom(2.5, duration);
    }
  }, [focusPath, graph, duration]);

  const drawNode = useCallback((node: GraphNode, ctx: CanvasRenderingContext2D, scale: number) => {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
    const x = node.x!;
    const y = node.y!;
    const related = !activePath || highlighted.has(node.path);
    const radius = 3 + Math.min(9, Math.sqrt(node.degree) * 1.5);
    const age = node.pulseAt === undefined ? Infinity : frameTime.current - node.pulseAt;
    const progress = Math.max(0, Math.min(1, age / 650));
    const popping = !reduced && age >= 0 && progress < 1;
    const spring = popping ? 1 - 0.65 * Math.exp(-7 * progress) * Math.cos(12 * progress) : 1;
    ctx.save();
    ctx.globalAlpha = related ? 1 : 0.16;
    if (popping) {
      ctx.beginPath();
      ctx.arc(x, y, radius + 4 + progress * 9, 0, Math.PI * 2);
      ctx.strokeStyle = palette.accent!;
      ctx.lineWidth = 2 / scale;
      ctx.globalAlpha *= (1 - progress) * 0.8;
      ctx.stroke();
      ctx.globalAlpha = related ? 1 : 0.16;
    }
    ctx.beginPath();
    ctx.arc(x, y, radius * spring, 0, Math.PI * 2);
    ctx.fillStyle = Object.hasOwn(strings.pageTypes, node.type) ? palette[node.type]! : palette.muted!;
    ctx.fill();
    if (node.path === activePath) {
      ctx.beginPath();
      ctx.arc(x, y, radius + 3 / scale, 0, Math.PI * 2);
      ctx.strokeStyle = palette.accent!;
      ctx.lineWidth = 1.5 / scale;
      ctx.stroke();
    }
    if (related && (node.path === activePath || scale >= (compact ? 2.4 : 1.8))) {
      const fontSize = 12 / scale;
      const label = strings.nodeTitle(node.title || node.path);
      ctx.font = `400 ${fontSize}px Geist, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const labelWidth = ctx.measureText(label).width;
      const top = y + radius + 5 / scale;
      ctx.fillStyle = palette.background!;
      ctx.fillRect(x - labelWidth / 2 - 3 / scale, top - 1 / scale, labelWidth + 6 / scale, fontSize + 3 / scale);
      ctx.fillStyle = palette.foreground!;
      ctx.fillText(label, x, top);
    }
    ctx.restore();
  }, [activePath, highlighted, palette, reduced, compact]);

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget || !engine.current) return;
    const api = engine.current;
    const center = api.centerAt();
    const step = 48 / api.zoom();
    if (['+', '=', '-', '0', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) manualMove();
    switch (event.key) {
      case '+': case '=': api.zoom(Math.min(12, api.zoom() * 1.3), duration); break;
      case '-': api.zoom(Math.max(0.08, api.zoom() / 1.3), duration); break;
      case '0': fit(); break;
      case 'ArrowLeft': api.centerAt(center.x - step, center.y, duration); break;
      case 'ArrowRight': api.centerAt(center.x + step, center.y, duration); break;
      case 'ArrowUp': api.centerAt(center.x, center.y - step, duration); break;
      case 'ArrowDown': api.centerAt(center.x, center.y + step, duration); break;
      case 'Escape': setHovered(null); break;
      default: return;
    }
    event.preventDefault();
  }

  return <div ref={container} className={`knowledge-canvas${compact ? ' knowledge-canvas-compact' : ''}`}
    role="region" aria-label={strings.canvasSummary(graph.nodes.length, graph.links.length)}
    tabIndex={0} onKeyDown={keyboard} onWheelCapture={manualMove}
    onPointerMoveCapture={event => { if (event.buttons) manualMove(); }}>
    {width > 0 && height > 0 && palette.background && <ForceGraph2D<GraphNode>
      ref={engine} width={width} height={height} graphData={graph}
      backgroundColor={palette.background} nodeId="path" nodeLabel={() => ''}
      nodeVal={node => 1 + node.degree} nodeCanvasObject={drawNode}
      nodePointerAreaPaint={(node, color, ctx, scale) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x ?? 0, node.y ?? 0, Math.max(3 + Math.min(9, Math.sqrt(node.degree) * 1.5), 9 / scale), 0, Math.PI * 2);
        ctx.fill();
      }}
      linkColor={link => activePath
        ? (link.from_path === activePath || link.to_path === activePath ? `${palette.accent}b0` : `${palette.border}60`)
        : `${palette.muted}40`}
      linkWidth={link => activePath && (link.from_path === activePath || link.to_path === activePath) ? 1.4 : 0.7}
      enablePointerInteraction enableNodeDrag linkHoverPrecision={0}
      minZoom={0.08} maxZoom={12} d3VelocityDecay={0.4}
      warmupTicks={reduced ? 80 : 0} cooldownTicks={reduced ? 0 : 80} cooldownTime={5000}
      onRenderFramePre={() => { frameTime.current = performance.now(); }}
      onNodeHover={node => setHovered(node?.path ?? null)}
      onNodeClick={node => void navigate(wikiPageUrl(brainId, node.path))}
      onEngineStop={() => {
        if (needsFit.current && !manuallyMoved.current) { needsFit.current = false; scheduleFit(); }
      }}
    />}
    {!compact && <div className="knowledge-canvas-controls">
      <Button variant="secondary" size="icon" title={strings.zoomOut} aria-label={strings.zoomOut} onClick={() => {
        manualMove();
        if (engine.current) engine.current.zoom(Math.max(0.08, engine.current.zoom() / 1.4), duration);
      }}><Minus size={17} /></Button>
      <Button variant="secondary" size="icon" title={strings.zoomIn} aria-label={strings.zoomIn} onClick={() => {
        manualMove();
        if (engine.current) engine.current.zoom(Math.min(12, engine.current.zoom() * 1.4), duration);
      }}><Plus size={17} /></Button>
      <Button variant="secondary" size="icon" title={strings.fit} aria-label={strings.fit} onClick={() => { manualMove(); fit(); }}><Maximize2 size={17} /></Button>
    </div>}
  </div>;
}
