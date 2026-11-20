import type { GraphEdge, GraphModel, GraphNode, GraphSubset } from './data';

/** "Show only neighbors": the pages at most `depth` links away from `path`. */
export type Scope = { path: string; depth: 1 | 2 };

export function adjacency(links: readonly GraphEdge[]): Map<string, Set<string>> {
  const result = new Map<string, Set<string>>();
  const add = (from: string, to: string) => {
    let set = result.get(from);
    if (!set) result.set(from, set = new Set());
    set.add(to);
  };
  for (const link of links) {
    add(link.from_path, link.to_path);
    add(link.to_path, link.from_path);
  }
  return result;
}

/** Linked pairs of pages: A → B and B → A count once, as the canvas draws one edge per pair. */
export function connectionCount(links: readonly GraphEdge[]): number {
  return new Set(links.map(link => JSON.stringify(link.from_path < link.to_path ? [link.from_path, link.to_path] : [link.to_path, link.from_path]))).size;
}

/** `start` and every page at most `depth` links away from it, in either direction. */
export function neighborhood(neighbors: ReadonlyMap<string, ReadonlySet<string>>, start: string, depth: number): Set<string> {
  const seen = new Set([start]);
  let frontier = [start];
  for (let step = 0; step < depth && frontier.length; step++) {
    const next: string[] = [];
    for (const path of frontier) {
      for (const neighbor of neighbors.get(path) ?? []) {
        if (seen.has(neighbor)) continue;
        seen.add(neighbor);
        next.push(neighbor);
      }
    }
    frontier = next;
  }
  return seen;
}

function restrict(nodes: GraphNode[], links: readonly GraphEdge[], keep: (node: GraphNode) => boolean): GraphSubset {
  const kept = nodes.filter(keep);
  const paths = new Set(kept.map(node => node.path));
  return { nodes: kept, links: links.filter(link => paths.has(link.from_path) && paths.has(link.to_path)) };
}

/** The pages and links a view draws: first the type filter, then the scope,
 * measured over the links that remain visible. A scope whose page is not
 * visible does not apply (`scoped` is false). Node objects are shared with
 * the model, so degrees always count the whole loaded graph. */
export function visibleGraph(model: GraphModel, filters: { type: string | null; scope: Scope | null }): GraphSubset & { scoped: boolean } {
  const typed = filters.type === null ? { nodes: model.nodes, links: model.links } : restrict(model.nodes, model.links, node => node.type === filters.type);
  const scope = filters.scope;
  if (!scope || !typed.nodes.some(node => node.path === scope.path)) return { ...typed, scoped: false };
  const keep = neighborhood(adjacency(typed.links), scope.path, scope.depth);
  return { ...restrict(typed.nodes, typed.links, node => keep.has(node.path)), scoped: true };
}

/** The pages that `path` links to and the pages that link to it, by title. */
export function pageLinks(model: Pick<GraphModel, 'nodes' | 'links'>, path: string): { outgoing: GraphNode[]; incoming: GraphNode[] } {
  const byPath = new Map(model.nodes.map(node => [node.path, node]));
  const outgoing = new Set<GraphNode>();
  const incoming = new Set<GraphNode>();
  for (const link of model.links) {
    if (link.from_path === path) outgoing.add(byPath.get(link.to_path)!);
    if (link.to_path === path) incoming.add(byPath.get(link.from_path)!);
  }
  const byTitle = (a: GraphNode, b: GraphNode) => (a.title || a.path).localeCompare(b.title || b.path);
  return { outgoing: [...outgoing].sort(byTitle), incoming: [...incoming].sort(byTitle) };
}

/** What the canvas emphasizes: the selected page wins over the hovered one. */
export type Emphasis = { active: string | null; selected: string | null; hovered: string | null; related: ReadonlySet<string> };

export function emphasis(neighbors: ReadonlyMap<string, ReadonlySet<string>>, selected: string | null, hovered: string | null): Emphasis {
  const active = selected ?? hovered;
  return { active, selected, hovered, related: active ? neighborhood(neighbors, active, 1) : new Set() };
}
