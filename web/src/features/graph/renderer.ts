// The WebGL side of the graph: one sigma instance over a graphology render
// graph, a ForceAtlas2 worker, and the camera. Browser only.
import Sigma, { DEFAULT_CAMERA_STATE } from 'sigma';
import type { CameraState, EdgeDisplayData, NodeDisplayData } from 'sigma/types';
import { Autofit, boundsOf, fitCamera } from './camera';
import { LayoutController } from './layout';
import type { Emphasis } from './selection';
import { graphPrimitives, graphSettings, graphStyles } from './sigma-config';
import { edgeLook, nodeLook, PULSE_MS, withAlpha, type GraphPalette, type LookState } from './style';
import type { Point, RenderEdge, RenderGraph, RenderNode, SyncResult } from './sync';

export type RendererCallbacks = {
  select: (path: string) => void;
  open: (path: string) => void;
  contextMenu: (path: string, point: Point) => void;
  pin: (path: string) => void;
};
type RendererOptions = {
  compact: boolean;
  reducedMotion: boolean;
  onLayoutSettled?: () => void;
  /** Read on every event, so the latest React props apply. */
  callbacks: () => RendererCallbacks;
};
type LookInputs = Omit<LookState, 'palette' | 'emphasis' | 'now'> & {
  palette: GraphPalette | null;
  selected: string | null;
  hovered: string | null;
};

const NO_EMPHASIS: Emphasis = { active: null, selected: null, hovered: null, related: new Set() };
const CAMERA_MS = 450;

export class GraphRenderer {
  readonly sigma: Sigma<RenderNode, RenderEdge>;
  readonly layout: LayoutController;
  private readonly graph: RenderGraph;
  private readonly options: RendererOptions;
  private readonly resize: ResizeObserver;
  private inputs: LookInputs;
  private state: LookState | null = null;
  /** Until the user moves the camera, positions rescale to fit the view. */
  private readonly autofit = new Autofit();
  private pulseFrame = 0;
  private draggedAt = -Infinity;

  constructor(container: HTMLElement, graph: RenderGraph, options: RendererOptions) {
    this.graph = graph;
    this.options = options;
    this.inputs = { palette: null, colorBy: 'type', communities: new Map(), selected: null, hovered: null, reducedMotion: options.reducedMotion };
    this.sigma = new Sigma<RenderNode, RenderEdge>(graph, container, {
      primitives: graphPrimitives(),
      styles: graphStyles,
      settings: graphSettings(options.compact, options.reducedMotion),
      nodeReducer: (key, _data, attributes) => this.nodeDisplay(key, attributes),
      edgeReducer: key => this.edgeDisplay(key),
    });
    this.layout = new LayoutController(graph, { animate: !options.reducedMotion, onSettled: options.onLayoutSettled });
    this.bindEvents();
    this.resize = new ResizeObserver(() => {
      this.sigma.resize();
      this.sigma.scheduleRefresh();
    });
    this.resize.observe(container);
  }

  kill() {
    cancelAnimationFrame(this.pulseFrame);
    this.resize.disconnect();
    this.layout.kill();
    this.sigma.kill();
  }

  /** Change what the reducers read, then redraw. */
  update(inputs: Partial<LookInputs>) {
    this.inputs = { ...this.inputs, ...inputs };
    if (inputs.reducedMotion !== undefined) {
      this.layout.setAnimate(!inputs.reducedMotion);
      const duration = inputs.reducedMotion ? 0 : 250;
      this.sigma.setSettings({ zoomDuration: duration, doubleClickZoomingDuration: duration, inertiaDuration: inputs.reducedMotion ? 0 : 200 });
    }
    this.refreshLook();
  }

  /** Call after `syncGraph`: restart the layout, redraw, and pulse new writes. */
  synced(result: SyncResult, pinnedChanged: boolean) {
    if (result.structureChanged || pinnedChanged) this.layout.restart(!result.initial);
    this.refreshLook();
    this.pulse();
  }

  focus(path: string) {
    if (!this.graph.hasNode(path)) return;
    const keys = [path, ...this.graph.neighbors(path)];
    this.markManual();
    // Zoom in at most 4x (5x for a page without links), so the context stays in view.
    const target = this.fit(keys, this.options.compact ? 24 : 96, { min: keys.length === 1 ? 0.2 : 0.25, max: 1.2 });
    if (target) this.animate(target);
  }

  fitAll() {
    this.markManual();
    const target = this.fit(this.graph.nodes(), this.sigma.getStagePadding() || 56, { min: 0.03, max: 4 });
    if (target) this.animate(target);
  }

  zoom(factor: number) {
    this.markManual();
    const camera = this.sigma.getCamera();
    const duration = this.inputs.reducedMotion ? 0 : 200;
    void (factor > 1 ? camera.zoomIn({ factor, duration }) : camera.zoomOut({ factor: 1 / factor, duration }));
  }

  /** Move the view by a share of its size; positive `dy` moves the view down. */
  pan(dx: number, dy: number) {
    this.markManual();
    const camera = this.sigma.getCamera();
    const { width, height } = this.sigma.getDimensions();
    const center = this.sigma.viewportToFramedGraph({ x: width / 2 + dx * width, y: height / 2 + dy * height });
    void camera.animate(center, { duration: this.inputs.reducedMotion ? 0 : 200 });
  }

  /** Viewport coordinates of a node, for menus. */
  nodePoint(path: string): Point | null {
    if (!this.graph.hasNode(path)) return null;
    return this.sigma.graphToViewport(this.graph.getNodeAttributes(path));
  }

  private bindEvents() {
    const { sigma, graph } = this;
    const callbacks = this.options.callbacks;
    sigma.on('enterNode', ({ node }) => this.update({ hovered: node }));
    sigma.on('leaveNode', () => this.update({ hovered: null }));
    sigma.on('clickNode', ({ node }) => {
      if (this.options.compact) callbacks().open(node);
      else if (!this.justDragged()) callbacks().select(node);
    });
    sigma.on('clickStage', () => {
      if (!this.options.compact && !this.justDragged()) callbacks().select('');
    });
    sigma.on('doubleClickNode', payload => {
      payload.preventSigmaDefault();
      callbacks().open(payload.node);
    });
    sigma.on('rightClickNode', ({ node, event }) => {
      if (this.options.compact) return;
      event.original.preventDefault();
      callbacks().select(node);
      callbacks().contextMenu(node, { x: event.x, y: event.y });
    });
    sigma.on('nodeDragStart', ({ node }) => {
      this.markManual();
      if (graph.getNodeAttribute(node, 'fixed')) this.layout.nudge();
      else {
        // Dragging pins the page, so the layout lets it stay where it is dropped.
        graph.setNodeAttribute(node, 'fixed', true);
        this.layout.restart(true);
        callbacks().pin(node);
      }
    });
    sigma.on('nodeDrag', () => this.layout.nudge());
    sigma.on('nodeDragEnd', () => {
      this.draggedAt = performance.now();
      this.layout.nudge(1200);
    });
    sigma.getCamera().on('updated', () => {
      if (this.autofit.cameraUpdated(performance.now())) this.keepFraming();
    });
  }

  /** The click that ends a drag is not a selection. */
  private justDragged() {
    return performance.now() - this.draggedAt < 250;
  }

  private markManual() {
    if (this.autofit.end()) this.keepFraming();
  }

  /** Keep the normalization in use; from now on the camera alone frames the
   * graph. (getBBox() is a placeholder between a refresh and its render.) */
  private keepFraming() {
    const normalize = this.sigma.getNormalizationFunction();
    const center = normalize.inverse({ x: 0.5, y: 0.5 });
    const half = normalize.ratio / 2;
    this.sigma.setCustomBBox({ x: [center.x - half, center.x + half], y: [center.y - half, center.y + half] });
  }

  private animate(target: CameraState) {
    const duration = this.inputs.reducedMotion ? 0 : CAMERA_MS;
    this.autofit.appMove(performance.now(), duration);
    void this.sigma.getCamera().animate(target, { duration, easing: 'cubicInOut' });
  }

  private fit(keys: readonly string[], padding: number, limits: { min: number; max: number }): CameraState | null {
    const bounds = boundsOf(keys.filter(key => this.graph.hasNode(key))
      .map(key => this.sigma.graphToFramedGraph(this.graph.getNodeAttributes(key))));
    if (!bounds) return null;
    const origin = this.sigma.framedGraphToViewport({ x: 0, y: 0 }, { cameraState: DEFAULT_CAMERA_STATE });
    const corner = this.sigma.framedGraphToViewport({ x: 1, y: 1 }, { cameraState: DEFAULT_CAMERA_STATE });
    const unit = { x: Math.abs(corner.x - origin.x), y: Math.abs(corner.y - origin.y) };
    return fitCamera(bounds, unit, this.sigma.getDimensions(), padding, limits);
  }

  private emphasis(): Emphasis {
    const has = (key: string | null) => key !== null && this.graph.hasNode(key) ? key : null;
    const selected = has(this.inputs.selected);
    const hovered = has(this.inputs.hovered);
    const active = selected ?? hovered;
    if (!active) return NO_EMPHASIS;
    return { active, selected, hovered, related: new Set([active, ...this.graph.neighbors(active)]) };
  }

  private refreshLook() {
    const { palette } = this.inputs;
    this.state = palette ? { ...this.inputs, palette, emphasis: this.emphasis(), now: performance.now() } : null;
    this.sigma.scheduleRefresh();
  }

  private nodeDisplay(key: string, attributes: RenderNode): Partial<NodeDisplayData> {
    const state = this.state;
    if (!state) return { visibility: 'hidden' };
    state.now = performance.now();
    const look = nodeLook(key, attributes, state);
    const display: Partial<NodeDisplayData> & { coreColor: string; glow: number } = {
      ...look,
      backdropColor: state.palette.surface,
      backdropShadowColor: withAlpha('#000000', state.palette.dark ? 0.45 : 0.1),
    };
    return display;
  }

  private edgeDisplay(key: string): Partial<EdgeDisplayData> {
    if (!this.state) return { visibility: 'hidden' };
    const [source, target] = this.graph.extremities(key);
    return edgeLook(source, target, this.state);
  }

  /** Redraw pulsing pages every frame until their pulse ends. */
  private pulse() {
    if (this.inputs.reducedMotion || this.pulseFrame) return;
    let previous: string[] = [];
    const tick = () => {
      const now = performance.now();
      const nodes = this.graph.filterNodes((_key, { pulseAt }) => pulseAt !== undefined && now - pulseAt < PULSE_MS);
      // One more redraw after a pulse ends restores the resting size.
      const redraw = [...new Set([...previous, ...nodes])].filter(key => this.graph.hasNode(key));
      previous = nodes;
      if (redraw.length) this.sigma.refresh({ partialGraph: { nodes: redraw }, schedule: true });
      this.pulseFrame = nodes.length ? requestAnimationFrame(tick) : 0;
    };
    this.pulseFrame = requestAnimationFrame(tick);
  }
}

