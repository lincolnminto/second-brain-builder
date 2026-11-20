import { UndirectedGraph } from 'graphology';
import louvain from 'graphology-communities-louvain';
import type { GraphSubset } from './data';

// A fixed seed keeps community colors stable between runs on the same graph.
function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/** Louvain communities of the visible pages, as color indexes. A page
 * without links (a community of one) gets -1.
 *
 * `previous` holds the last known index of each page. A community keeps the
 * index that most of its pages had, so colors stay put when a live update or
 * a filter changes the graph; the largest community wins a contested index.
 * A new community gets the smallest free index (the largest new one first). */
export function communityIndexes(subset: GraphSubset, previous: ReadonlyMap<string, number> = new Map()): Map<string, number> {
  const graph = new UndirectedGraph();
  const nodes = [...subset.nodes].sort((a, b) => a.path.localeCompare(b.path));
  for (const node of nodes) graph.addNode(node.path);
  for (const link of subset.links) graph.mergeEdge(link.from_path, link.to_path);
  const result = new Map<string, number>();
  if (!graph.size) {
    for (const node of nodes) result.set(node.path, -1);
    return result;
  }
  const mapping = louvain(graph, { rng: seededRandom(1) });
  const members = new Map<number, string[]>();
  for (const node of nodes) {
    const id = mapping[node.path]!;
    members.set(id, [...members.get(id) ?? [], node.path]);
  }
  const ranked = [...members.values()].sort((a, b) => b.length - a.length || a[0]!.localeCompare(b[0]!));
  const groups = ranked.filter(group => group.length > 1);
  const taken = new Set<number>();
  const colors = new Map<string[], number>();
  for (const group of groups) {
    const votes = new Map<number, number>();
    for (const path of group) {
      const color = previous.get(path);
      if (color !== undefined && color >= 0 && !taken.has(color)) votes.set(color, (votes.get(color) ?? 0) + 1);
    }
    const best = [...votes].sort(([a, countA], [b, countB]) => countB - countA || a - b)[0];
    if (!best) continue;
    taken.add(best[0]);
    colors.set(group, best[0]);
  }
  let free = 0;
  for (const group of groups) {
    if (colors.has(group)) continue;
    while (taken.has(free)) free++;
    taken.add(free);
    colors.set(group, free);
  }
  for (const group of ranked) {
    const color = colors.get(group) ?? -1;
    for (const path of group) result.set(path, color);
  }
  return result;
}
