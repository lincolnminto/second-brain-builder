import { EDGE_STRIDE, NODE_STRIDE, type LayoutRequest, type LayoutResult, type LayoutSettings } from './layout-protocol';
import type { RenderGraph } from './sync';

/** ForceAtlas2 settings close to graphology's `inferSettings`. An incremental
 * run (live update, pin, drag) moves slower, so the picture stays calm. */
export function layoutSettings(order: number, incremental: boolean): LayoutSettings {
  return {
    linLogMode: false, outboundAttractionDistribution: false, adjustSizes: false, edgeWeightInfluence: 1,
    scalingRatio: 10, strongGravityMode: true, gravity: 0.05,
    slowDown: (1 + Math.log(Math.max(1, order))) * (incremental ? 3 : 1),
    barnesHutOptimize: order > 600, barnesHutTheta: 0.6,
  };
}

/** The most time one run may take, in milliseconds. */
export function layoutBudget(order: number, incremental: boolean): number {
  return incremental ? 1600 : Math.min(7000, 2000 + order * 2);
}

/** The ForceAtlas2 input matrices for the current graph. Pinned nodes are fixed. */
export function layoutMatrices(graph: RenderGraph): { keys: string[]; nodes: Float32Array<ArrayBuffer>; edges: Float32Array<ArrayBuffer> } {
  const keys = graph.nodes();
  const offset = new Map(keys.map((key, index) => [key, index * NODE_STRIDE]));
  const nodes = new Float32Array(keys.length * NODE_STRIDE);
  keys.forEach((key, index) => {
    const { x, y, fixed } = graph.getNodeAttributes(key);
    const at = index * NODE_STRIDE;
    nodes.set([x, y, 0, 0, 0, 0, 1, 1, 1, fixed ? 1 : 0], at);
  });
  const edges = new Float32Array(graph.size * EDGE_STRIDE);
  let at = 0;
  graph.forEachEdge((_key, _attributes, source, target) => {
    const from = offset.get(source)!;
    const to = offset.get(target)!;
    edges.set([from, to, 1], at);
    // Mass is 1 + degree, as in graphology's own matrices.
    nodes[from + 6] = nodes[from + 6]! + 1;
    nodes[to + 6] = nodes[to + 6]! + 1;
    at += EDGE_STRIDE;
  });
  return { keys, nodes, edges };
}

/** Write layout positions to the graph. Pinned nodes keep theirs. */
export function applyPositions(graph: RenderGraph, index: ReadonlyMap<string, number>, xy: Float32Array) {
  graph.updateEachNodeAttributes((key, attributes) => {
    const at = index.get(key);
    if (at === undefined || attributes.fixed) return attributes;
    const x = xy[at * 2]!;
    const y = xy[at * 2 + 1]!;
    if (Number.isFinite(x) && Number.isFinite(y)) {
      attributes.x = x;
      attributes.y = y;
    }
    return attributes;
  }, { attributes: ['x', 'y'] });
}

/** Mean movement per node between two results, relative to the layout size. */
export function relativeShift(previous: Float32Array | null, next: Float32Array): number {
  if (!previous || previous.length !== next.length || !next.length) return Infinity;
  let shift = 0;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let index = 0; index < next.length; index += 2) {
    const x = next[index]!;
    const y = next[index + 1]!;
    shift += Math.abs(x - previous[index]!) + Math.abs(y - previous[index + 1]!);
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const extent = Math.max(maxX - minX, maxY - minY, 1);
  return shift / (next.length / 2) / extent;
}

/** Iterations per step. Each applied step costs the renderer a pass over all
 * nodes, so a large graph takes more iterations per step and fewer steps. */
export function stepIterations(order: number, animate: boolean): number {
  if (!animate) return 25;
  return order > 1200 ? 3 : order > 400 ? 2 : 1;
}

const SETTLED_SHIFT = 0.0004;
const SETTLED_STEPS = 20;
const MIN_RUN_MS = 500;

export function createLayoutWorker(): Worker {
  return new Worker(new URL('./layout.worker.ts', import.meta.url), { type: 'module' });
}

type LayoutOptions = {
  /** False for reduced motion: run to the end, then apply the positions once. */
  animate: boolean;
  createWorker?: () => Worker;
  onSettled?: () => void;
};

/** Runs ForceAtlas2 in a worker over a render graph, until the layout
 * settles or its time budget ends. Restart it after a structure change. */
export class LayoutController {
  private worker: Worker | null = null;
  private generation = 0;
  private index = new Map<string, number>();
  private fixed: number[] = [];
  private keys: string[] = [];
  private startedAt = 0;
  private deadline = 0;
  private calmSteps = 0;
  private last: Float32Array | null = null;
  private running = false;
  private incremental = false;
  private frame = 0;
  private readonly graph: RenderGraph;
  private readonly options: LayoutOptions;

  constructor(graph: RenderGraph, options: LayoutOptions) {
    this.graph = graph;
    this.options = options;
  }

  get isRunning() { return this.running; }

  setAnimate(animate: boolean) {
    this.options.animate = animate;
  }

  /** Start a run from the graph's positions. A run in flight keeps its
   * progress, and a full run that a live update interrupts goes on as a full
   * run (an incremental run is slower, for small changes to a laid-out graph). */
  restart(incremental: boolean) {
    if (!this.graph.order) {
      this.stop();
      return;
    }
    const continuesFull = incremental && this.running && !this.incremental;
    this.flush();
    this.worker ??= this.spawn();
    const { keys, nodes, edges } = layoutMatrices(this.graph);
    const now = performance.now();
    this.incremental = incremental && !continuesFull;
    this.generation++;
    this.keys = keys;
    this.index = new Map(keys.map((key, index) => [key, index]));
    this.fixed = keys.flatMap((key, index) => this.graph.getNodeAttribute(key, 'fixed') ? [index] : []);
    this.post({ type: 'load', generation: this.generation, nodes: nodes.buffer, edges: edges.buffer, settings: layoutSettings(keys.length, this.incremental) }, [nodes.buffer, edges.buffer]);
    this.startedAt = now;
    this.deadline = continuesFull
      ? Math.max(this.deadline, now + layoutBudget(keys.length, true))
      : now + layoutBudget(keys.length, this.incremental);
    this.calmSteps = 0;
    this.last = null;
    this.running = true;
    cancelAnimationFrame(this.frame);
    this.step();
  }

  /** Write the positions of the run in flight to the graph. With reduced
   * motion, steps do not move the nodes, so call this before the graph
   * changes (new pages are placed next to their linked pages). */
  flush() {
    if (this.running && !this.options.animate && this.last) applyPositions(this.graph, this.index, this.last);
  }

  /** Keep a running layout going, or start a short incremental run. */
  nudge(milliseconds = 800) {
    if (!this.running) this.restart(true);
    this.deadline = Math.max(this.deadline, performance.now() + milliseconds);
    this.calmSteps = 0;
  }

  stop() {
    this.flush();
    this.running = false;
    cancelAnimationFrame(this.frame);
  }

  kill() {
    this.stop();
    this.worker?.terminate();
    this.worker = null;
  }

  private spawn() {
    const worker = (this.options.createWorker ?? createLayoutWorker)();
    worker.onmessage = (event: MessageEvent<LayoutResult>) => this.receive(event.data);
    return worker;
  }

  private post(message: LayoutRequest, transfer: Transferable[] = []) {
    this.worker?.postMessage(message, transfer);
  }

  private step() {
    // Pinned nodes follow the graph (the user may be dragging one).
    const moved = this.fixed.flatMap(index => {
      const key = this.keys[index]!;
      if (!this.graph.hasNode(key)) return [];
      const { x, y } = this.graph.getNodeAttributes(key);
      return [index, x, y];
    });
    this.post({ type: 'step', generation: this.generation, iterations: stepIterations(this.keys.length, this.options.animate), moved });
  }

  private receive(result: LayoutResult) {
    if (result.generation !== this.generation || !this.running) return;
    const xy = new Float32Array(result.xy);
    const now = performance.now();
    this.calmSteps = relativeShift(this.last, xy) < SETTLED_SHIFT ? this.calmSteps + 1 : 0;
    this.last = xy;
    if (this.options.animate) applyPositions(this.graph, this.index, xy);
    const settled = this.calmSteps >= (this.options.animate ? SETTLED_STEPS : 2) && now - this.startedAt >= MIN_RUN_MS;
    if (settled || now >= this.deadline) {
      this.stop();
      this.options.onSettled?.();
      return;
    }
    if (this.options.animate) this.frame = requestAnimationFrame(() => this.step());
    else this.step();
  }
}
