import type { Schema } from '../../api/client';
import { sourceSlug, sourceViewUrl } from '../wiki/resolution';

export type GraphNode = Schema['GraphNodeView'] & {
  /** Distinct neighbors in the whole loaded graph, in either direction. */
  degree: number;
  /** `performance.now()` of the last live write, for the arrival pulse. */
  pulseAt?: number;
};
export type GraphEdge = Schema['GraphEdgeView'];
export type GraphModel = {
  nodes: GraphNode[];
  /** Directed references, including references to pages that are not loaded yet. */
  edges: GraphEdge[];
  /** Directed references whose two pages are loaded. */
  links: GraphEdge[];
  /** Undirected adjacency of `links`. */
  neighbors: Map<string, Set<string>>;
};
/** The part of a model that one view draws. */
export type GraphSubset = Pick<GraphModel, 'nodes' | 'links'>;

export function emptyGraph(): GraphModel {
  return { nodes: [], edges: [], links: [], neighbors: new Map() };
}

function assemble(nodes: GraphNode[], edges: GraphEdge[]): GraphModel {
  const loaded = new Set(nodes.map(node => node.path));
  const unique = new Map<string, GraphEdge>();
  const neighbors = new Map(nodes.map(node => [node.path, new Set<string>()]));
  const links: GraphEdge[] = [];
  for (const edge of edges) {
    const key = JSON.stringify([edge.from_path, edge.to_path]);
    if (edge.from_path === edge.to_path || unique.has(key)) continue;
    unique.set(key, edge);
    if (!loaded.has(edge.from_path) || !loaded.has(edge.to_path)) continue;
    links.push(edge);
    neighbors.get(edge.from_path)!.add(edge.to_path);
    neighbors.get(edge.to_path)!.add(edge.from_path);
  }
  for (const node of nodes) node.degree = neighbors.get(node.path)!.size;
  return { nodes, edges: [...unique.values()], links, neighbors };
}

// Reuse node objects by path, so views keyed by node identity (and the pulse
// timestamps) survive a refetch. Layout positions live with the canvas.
export function mergeSnapshot(current: GraphModel, snapshot: Schema['GraphView']): GraphModel {
  const previous = new Map(current.nodes.map(node => [node.path, node]));
  const nodes = [...new Map(snapshot.nodes.map(page => {
    const node = previous.get(page.path) ?? { degree: 0, ...page };
    Object.assign(node, page);
    return [page.path, node] as const;
  })).values()];
  const paths = new Set(nodes.map(node => node.path));
  // Keep live references to pages that the snapshot does not know yet.
  const pending = current.edges.filter(edge =>
    paths.has(edge.from_path) && !previous.has(edge.to_path));
  return assemble(nodes, [...snapshot.edges, ...pending]);
}

/** Apply a `page_written` or `page_deleted` event. With `addPages: false`, a
 * write only updates pages that are already loaded (a Folder view gets new
 * pages from its refetch, because only the server knows what they cite). */
export function applyPageEvent(current: GraphModel, event: Schema['JobEventView'], now: number, addPages = true): GraphModel {
  const { kind, data } = event;
  const path = data.path;
  if (typeof path !== 'string' || !path) return current;
  if (kind === 'page_deleted') {
    if (!current.nodes.some(node => node.path === path) && !current.edges.some(edge => edge.from_path === path || edge.to_path === path)) return current;
    return assemble(current.nodes.filter(node => node.path !== path),
      current.edges.filter(edge => edge.from_path !== path && edge.to_path !== path));
  }
  if (kind !== 'page_written' || typeof data.title !== 'string' || typeof data.type !== 'string' || !Array.isArray(data.links)) return current;
  const oldNode = current.nodes.find(node => node.path === path);
  if (!oldNode && !addPages) return current;
  const node: GraphNode = oldNode ?? { path, title: data.title, type: data.type, degree: 0 };
  Object.assign(node, { title: data.title, type: data.type, pulseAt: now });
  const edges = current.edges.filter(edge => edge.from_path !== path);
  for (const target of data.links) {
    if (typeof target === 'string' && target) edges.push({ from_path: path, to_path: target });
  }
  return assemble(oldNode ? current.nodes : [...current.nodes, node], edges);
}

export function wikiPageUrl(brainId: string, path: string) {
  return `/brains/${encodeURIComponent(brainId)}/wiki/${path.split('/').map(encodeURIComponent).join('/')}`;
}

/** The Source view of a Canonical Document (`sources/<folder>/.../<slug>/<slug>.md`). */
export function sourceUrl(brainId: string, path: string): string | null {
  const slug = sourceSlug(path);
  return slug ? sourceViewUrl(brainId, slug) : null;
}

/** Where a page opens. A Canonical Document can be several MB, so it opens in
 * its Source view, which loads it in segments. Wiki Pages open in the Wiki. */
export function pageUrl(brainId: string, node: Pick<GraphNode, 'path' | 'type'>): string {
  return (node.type === 'source' ? sourceUrl(brainId, node.path) : null) ?? wikiPageUrl(brainId, node.path);
}

/** True when a page opens in its Source view (see `pageUrl`). */
export function opensSourceView(brainId: string, node: Pick<GraphNode, 'path' | 'type'>): boolean {
  return node.type === 'source' && sourceUrl(brainId, node.path) !== null;
}
