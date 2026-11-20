import type { AbstractGraph } from 'graphology-types';
import type { GraphSubset } from './data';
import { adjacency } from './selection';
import { strings } from './strings';
import { nodeSize } from './style';

export type Point = { x: number; y: number };
export type RenderNode = {
  x: number;
  y: number;
  /** Sigma size: the visible disc plus its glow. */
  size: number;
  label: string;
  type: string;
  degree: number;
  /** One of the best-connected visible pages: its label pill always shows. */
  hub: boolean;
  /** Pinned: the layout does not move it. */
  fixed: boolean;
  pulseAt?: number;
};
export type RenderEdge = { curvature: number };
export type RenderGraph = AbstractGraph<RenderNode, RenderEdge>;

export type SyncOptions = {
  /** Last known positions by path; filled when nodes leave the graph. */
  positions: Map<string, Point>;
  pinned: ReadonlySet<string>;
  compact: boolean;
  /** How many hubs get a permanent label pill. */
  hubs: number;
};
export type SyncResult = {
  added: string[];
  removed: string[];
  /** Nodes or edges were added or removed: the layout must run. */
  structureChanged: boolean;
  /** Nothing was drawn before and no position was known: a full layout run, not an incremental one. */
  initial: boolean;
};

export const EDGE_CURVATURE = 0.16;
const SPACING = 12;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** Rendering uses one undirected edge per linked pair. */
export function edgeKey(a: string, b: string): string {
  return JSON.stringify(a < b ? [a, b] : [b, a]);
}

function hash(text: string): number {
  let value = 2166136261;
  for (let index = 0; index < text.length; index++) value = Math.imul(value ^ text.charCodeAt(index), 16777619);
  return (value >>> 0) / 4294967296;
}

/** A sunflower spiral: an even, deterministic start for a fresh layout. */
export function spiralPoint(index: number): Point {
  const radius = SPACING * Math.sqrt(index + 0.5);
  return { x: radius * Math.cos(index * GOLDEN_ANGLE), y: radius * Math.sin(index * GOLDEN_ANGLE) };
}

function topHubs(subset: GraphSubset, count: number): Set<string> {
  return new Set([...subset.nodes].filter(node => node.degree >= 3)
    .sort((a, b) => b.degree - a.degree || a.path.localeCompare(b.path)).slice(0, count).map(node => node.path));
}

/** Make the render graph match a subset without a layout reset: nodes that
 * stay keep their positions, returning nodes get their last position, and
 * new nodes start next to a linked page (or at the edge of the graph). */
export function syncGraph(graph: RenderGraph, subset: GraphSubset, options: SyncOptions): SyncResult {
  const wanted = new Map(subset.nodes.map(node => [node.path, node]));
  const initial = graph.order === 0;
  const removed = graph.filterNodes(key => !wanted.has(key));
  for (const key of removed) {
    const { x, y } = graph.getNodeAttributes(key);
    options.positions.set(key, { x, y });
    graph.dropNode(key);
  }

  const hubs = topHubs(subset, options.hubs);
  const attributes = (path: string) => {
    const node = wanted.get(path)!;
    return {
      label: strings.nodeTitle(node.title || node.path), type: node.type, degree: node.degree,
      size: nodeSize(node.degree, options.compact), hub: hubs.has(path), fixed: options.pinned.has(path),
      pulseAt: node.pulseAt,
    };
  };
  for (const key of graph.nodes()) {
    const next = attributes(key);
    const current = graph.getNodeAttributes(key);
    if ((Object.keys(next) as (keyof typeof next)[]).some(name => current[name] !== next[name])) graph.mergeNodeAttributes(key, next);
  }

  const added: string[] = [];
  const unplaced: string[] = [];
  // A fresh layout: nothing was drawn before and no position is known.
  const fresh = initial && subset.nodes.every(node => !options.positions.has(node.path));
  for (const node of subset.nodes) {
    if (graph.hasNode(node.path)) continue;
    const known = options.positions.get(node.path);
    if (known) {
      graph.addNode(node.path, { ...attributes(node.path), ...known });
      added.push(node.path);
    } else unplaced.push(node.path);
  }
  if (unplaced.length) {
    const neighbors = adjacency(subset.links);
    let bounds: { cx: number; cy: number; radius: number } | null = null;
    unplaced.forEach((path, index) => {
      let point: Point;
      const anchors = [...neighbors.get(path) ?? []].filter(neighbor => graph.hasNode(neighbor));
      if (fresh) point = spiralPoint(index);
      else if (anchors.length) {
        const center = anchors.reduce((sum, anchor) => {
          const { x, y } = graph.getNodeAttributes(anchor);
          return { x: sum.x + x / anchors.length, y: sum.y + y / anchors.length };
        }, { x: 0, y: 0 });
        const angle = hash(path) * Math.PI * 2;
        point = { x: center.x + Math.cos(angle) * SPACING, y: center.y + Math.sin(angle) * SPACING };
      } else {
        bounds ??= graphBounds(graph);
        const angle = hash(path) * Math.PI * 2;
        point = { x: bounds.cx + Math.cos(angle) * bounds.radius, y: bounds.cy + Math.sin(angle) * bounds.radius };
      }
      graph.addNode(path, { ...attributes(path), ...point });
      added.push(path);
    });
  }

  const wantedEdges = new Map(subset.links.map(link => [edgeKey(link.from_path, link.to_path), link]));
  const staleEdges = graph.filterEdges(key => !wantedEdges.has(key));
  for (const key of staleEdges) graph.dropEdge(key);
  let addedEdges = 0;
  for (const [key, link] of wantedEdges) {
    if (graph.hasEdge(key)) continue;
    graph.addEdgeWithKey(key, link.from_path, link.to_path, { curvature: EDGE_CURVATURE });
    addedEdges++;
  }
  return { added, removed, structureChanged: added.length + removed.length + staleEdges.length + addedEdges > 0, initial: fresh };
}

function graphBounds(graph: RenderGraph): { cx: number; cy: number; radius: number } {
  if (!graph.order) return { cx: 0, cy: 0, radius: 0 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  graph.forEachNode((_key, { x, y }) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  });
  return { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, radius: Math.max(maxX - minX, maxY - minY) / 2 + SPACING * 2 };
}

/** Save every position, for example before the canvas unmounts. */
export function rememberPositions(graph: RenderGraph, positions: Map<string, Point>) {
  graph.forEachNode((key, { x, y }) => positions.set(key, { x, y }));
}
