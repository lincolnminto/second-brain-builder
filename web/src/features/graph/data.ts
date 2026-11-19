import type { Schema } from '../../api/client';

export type GraphNode = Schema['GraphNodeView'] & {
  id: string;
  degree: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  pulseAt?: number;
};
export type GraphEdge = Schema['GraphEdgeView'];
export type GraphLink = GraphEdge & {
  source: string | GraphNode;
  target: string | GraphNode;
};
export type GraphModel = {
  nodes: GraphNode[];
  // Keep unresolved endpoints here, but never send them to the force engine.
  edges: GraphEdge[];
  links: GraphLink[];
  neighbors: Map<string, Set<string>>;
};

export function emptyGraph(): GraphModel {
  return { nodes: [], edges: [], links: [], neighbors: new Map() };
}

function assemble(nodes: GraphNode[], edges: GraphEdge[]): GraphModel {
  const byPath = new Map(nodes.map(node => [node.path, node]));
  const unique = new Map<string, GraphEdge>();
  const neighbors = new Map(nodes.map(node => [node.path, new Set<string>()]));
  const links: GraphLink[] = [];
  for (const edge of edges) {
    const key = JSON.stringify([edge.from_path, edge.to_path]);
    if (edge.from_path === edge.to_path || unique.has(key)) continue;
    unique.set(key, edge);
    if (!byPath.has(edge.from_path) || !byPath.has(edge.to_path)) continue;
    links.push({ ...edge, source: edge.from_path, target: edge.to_path });
    neighbors.get(edge.from_path)!.add(edge.to_path);
    neighbors.get(edge.to_path)!.add(edge.from_path);
  }
  for (const node of nodes) node.degree = neighbors.get(node.path)!.size;
  return { nodes, edges: [...unique.values()], links, neighbors };
}

// The force engine mutates nodes (coordinates, velocity) and link endpoints.
// Reuse nodes by path, and rebuild links from canonical paths, never from those
// mutated endpoints. A refetch can then reconcile content without a layout reset.
export function mergeSnapshot(current: GraphModel, snapshot: Schema['GraphView']): GraphModel {
  const previous = new Map(current.nodes.map(node => [node.path, node]));
  const nodes = [...new Map(snapshot.nodes.map(page => {
    const node = previous.get(page.path) ?? { id: page.path, degree: 0, ...page };
    Object.assign(node, page);
    return [page.path, node] as const;
  })).values()];
  const paths = new Set(nodes.map(node => node.path));
  const pending = current.edges.filter(edge =>
    paths.has(edge.from_path) && !previous.has(edge.to_path));
  return assemble(nodes, [...snapshot.edges, ...pending]);
}

export function applyPageEvent(current: GraphModel, event: Schema['JobEventView'], now: number): GraphModel {
  const { kind, data } = event;
  const path = data.path;
  if (typeof path !== 'string' || !path) return current;
  if (kind === 'page_deleted') {
    return assemble(current.nodes.filter(node => node.path !== path),
      current.edges.filter(edge => edge.from_path !== path && edge.to_path !== path));
  }
  if (kind !== 'page_written' || typeof data.title !== 'string' || typeof data.type !== 'string' || !Array.isArray(data.links)) return current;
  const oldNode = current.nodes.find(node => node.path === path);
  const node: GraphNode = oldNode ?? { id: path, path, title: data.title, type: data.type, degree: 0 };
  Object.assign(node, { title: data.title, type: data.type, pulseAt: now });
  if (!oldNode) {
    const targets = new Set(data.links);
    const incoming = new Set(current.edges.filter(edge => edge.to_path === path).map(edge => edge.from_path));
    const neighbor = current.nodes.find(candidate => (targets.has(candidate.path) || incoming.has(candidate.path)) && Number.isFinite(candidate.x) && Number.isFinite(candidate.y));
    if (neighbor) {
      // Seed near a connected page; don't disturb existing coordinates.
      node.x = neighbor.x! + 14;
      node.y = neighbor.y! + 14;
    }
  }
  const edges = current.edges.filter(edge => edge.from_path !== path);
  for (const target of data.links) {
    if (typeof target === 'string' && target) edges.push({ from_path: path, to_path: target });
  }
  return assemble(oldNode ? current.nodes : [...current.nodes, node], edges);
}

export function filterGraph(model: GraphModel, type: string | null) {
  if (type === null) return { nodes: model.nodes, links: model.links };
  const nodes = model.nodes.filter(node => node.type === type);
  const paths = new Set(nodes.map(node => node.path));
  return { nodes, links: model.links.filter(link => paths.has(link.from_path) && paths.has(link.to_path)) };
}

export function wikiPageUrl(brainId: string, path: string) {
  return `/brains/${encodeURIComponent(brainId)}/wiki/${path.split('/').map(encodeURIComponent).join('/')}`;
}
