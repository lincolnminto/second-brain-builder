import { UndirectedGraph } from 'graphology';
import { describe, expect, it } from 'vitest';
import { communityIndexes } from './communities';
import { emptyGraph, mergeSnapshot, type GraphSubset } from './data';
import { applyPositions, layoutMatrices } from './layout';
import { NODE_STRIDE } from './layout-protocol';
import { visibleGraph } from './selection';
import { edgeKey, syncGraph, type Point, type RenderEdge, type RenderNode, type SyncOptions } from './sync';

const page = (name: string, type = 'concept') => ({ path: `wiki/${name}.md`, title: name, type });
const edge = (from: string, to: string) => ({ from_path: `wiki/${from}.md`, to_path: `wiki/${to}.md` });
const model = (names: string[], edges: ReturnType<typeof edge>[]) => mergeSnapshot(emptyGraph(), { nodes: names.map(name => page(name)), edges });

function setup(overrides: Partial<SyncOptions> = {}) {
  const graph = new UndirectedGraph<RenderNode, RenderEdge>();
  const options: SyncOptions = { positions: new Map<string, Point>(), pinned: new Set(), compact: false, hubs: 1, ...overrides };
  return { graph, options, sync: (subset: GraphSubset) => syncGraph(graph, subset, options) };
}
const position = (graph: UndirectedGraph<RenderNode, RenderEdge>, name: string) => {
  const { x, y } = graph.getNodeAttributes(`wiki/${name}.md`);
  return { x, y };
};

describe('render graph sync', () => {
  it('starts a fresh layout with distinct positions and one undirected edge per linked pair', () => {
    const { graph, sync } = setup();
    const result = sync(model(['a', 'b', 'c'], [edge('a', 'b'), edge('b', 'a'), edge('b', 'c')]));
    expect(result).toMatchObject({ structureChanged: true, initial: true, removed: [] });
    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);
    expect(graph.hasEdge(edgeKey('wiki/b.md', 'wiki/a.md'))).toBe(true);
    const points = graph.mapNodes((_key, { x, y }) => `${x.toFixed(3)},${y.toFixed(3)}`);
    expect(new Set(points).size).toBe(3);
  });

  it('adds a live page next to the page it links to without moving the others', () => {
    const { graph, sync } = setup();
    sync(model(['a', 'b'], [edge('a', 'b')]));
    graph.mergeNodeAttributes('wiki/a.md', { x: 500, y: -300 });
    graph.mergeNodeAttributes('wiki/b.md', { x: -40, y: 20 });
    const result = sync(model(['a', 'b', 'c'], [edge('a', 'b'), edge('c', 'a')]));
    expect(result).toMatchObject({ added: ['wiki/c.md'], structureChanged: true, initial: false });
    expect(position(graph, 'a')).toEqual({ x: 500, y: -300 });
    expect(position(graph, 'b')).toEqual({ x: -40, y: 20 });
    const c = position(graph, 'c');
    expect(Math.hypot(c.x - 500, c.y + 300)).toBeLessThan(20);
  });

  it('keeps positions across a filter, restoring hidden pages where they were', () => {
    const { graph, options, sync } = setup();
    const full = model(['a', 'b', 'c'], [edge('a', 'b'), edge('b', 'c')]);
    sync(full);
    graph.mergeNodeAttributes('wiki/c.md', { x: 123, y: 456 });
    const filtered = sync(visibleGraph(full, { type: null, scope: { path: 'wiki/a.md', depth: 1 } }));
    expect(filtered.removed).toEqual(['wiki/c.md']);
    expect(graph.size).toBe(1);
    expect(options.positions.get('wiki/c.md')).toEqual({ x: 123, y: 456 });
    sync(full);
    expect(position(graph, 'c')).toEqual({ x: 123, y: 456 });
  });

  it('removes pages without one drop event per page or link, which sigma answers with a full re-index', () => {
    const { graph, sync } = setup();
    const names = Array.from({ length: 60 }, (_, index) => `p${index}`);
    sync(model(names, names.slice(1).map(name => edge('p0', name))));
    graph.mergeNodeAttributes('wiki/p1.md', { x: 321, y: -123 });
    const drops: string[] = [];
    graph.on('nodeDropped', ({ key }) => drops.push(key));
    graph.on('edgeDropped', ({ key }) => drops.push(key));

    const result = sync(model(['p0', 'p1', 'p2'], [edge('p0', 'p1'), edge('p0', 'p2')]));

    expect(drops).toEqual([]);
    expect(result).toMatchObject({ structureChanged: true });
    expect(result.removed).toHaveLength(57);
    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);
    expect(position(graph, 'p1')).toEqual({ x: 321, y: -123 });
  });

  it('reports no structure change for the same pages, so the layout does not restart', () => {
    const { sync } = setup();
    const subset = model(['a', 'b'], [edge('a', 'b')]);
    sync(subset);
    expect(sync(subset)).toMatchObject({ structureChanged: false, added: [], removed: [] });
  });

  it('updates titles, degree sizes, pins, hubs, and live pulses in place', () => {
    const { graph, options, sync } = setup();
    const graphModel = model(['a', 'b', 'c', 'd'], [edge('a', 'b'), edge('a', 'c'), edge('a', 'd')]);
    sync(graphModel);
    expect(graph.getNodeAttributes('wiki/a.md')).toMatchObject({ hub: true, degree: 3, fixed: false });
    expect(graph.getNodeAttribute('wiki/a.md', 'size')).toBeGreaterThan(graph.getNodeAttribute('wiki/b.md', 'size'));
    Object.assign(graphModel.nodes[1]!, { title: 'Birds', pulseAt: 42 });
    options.pinned = new Set(['wiki/b.md']);
    expect(sync(graphModel).structureChanged).toBe(false);
    expect(graph.getNodeAttributes('wiki/b.md')).toMatchObject({ label: 'Birds', pulseAt: 42, fixed: true, hub: false });
  });
});

describe('layout input and output', () => {
  it('fixes pinned pages in the ForceAtlas2 matrices, weights mass by degree, and never moves them', () => {
    const { graph, sync } = setup({ pinned: new Set(['wiki/a.md']) });
    sync(model(['a', 'b', 'c'], [edge('a', 'b'), edge('a', 'c')]));
    const { keys, nodes, edges } = layoutMatrices(graph);
    const a = keys.indexOf('wiki/a.md') * NODE_STRIDE;
    expect(nodes[a + 9]).toBe(1);
    expect(nodes[a + 6]).toBe(3);
    expect(edges).toHaveLength(6);
    const before = position(graph, 'a');
    const xy = new Float32Array(keys.flatMap(() => [7, 9]));
    applyPositions(graph, new Map(keys.map((key, index) => [key, index])), xy);
    expect(position(graph, 'a')).toEqual(before);
    expect(position(graph, 'b')).toEqual({ x: 7, y: 9 });
  });
});

describe('communities', () => {
  it('finds linked groups, gives the largest index 0, and leaves unlinked pages without a community', () => {
    const triangle = (names: string[]) => [edge(names[0]!, names[1]!), edge(names[1]!, names[2]!), edge(names[2]!, names[0]!)];
    const subset = model(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'alone'], [
      ...triangle(['a', 'b', 'c']), edge('a', 'd'), edge('b', 'd'), edge('c', 'd'),
      ...triangle(['e', 'f', 'g']), edge('g', 'h'), edge('d', 'e'),
    ]);
    const communities = communityIndexes(subset);
    const of = (name: string) => communities.get(`wiki/${name}.md`);
    expect(new Set(['a', 'b', 'c', 'd'].map(of))).toEqual(new Set([0]));
    expect(new Set(['e', 'f', 'g', 'h'].map(of))).toEqual(new Set([1]));
    expect(of('alone')).toBe(-1);
    expect(communityIndexes(subset)).toEqual(communities);
  });

  it('keeps each community\'s color when a live update or a filter changes the graph', () => {
    const clique = (names: string[]) => names.flatMap((from, index) => names.slice(index + 1).map(to => edge(from, to)));
    const big = ['a1', 'a2', 'a3', 'a4'];
    const small = ['b1', 'b2', 'b3'];
    const first = communityIndexes(model([...big, ...small], [...clique(big), ...clique(small)]));
    const of = (map: Map<string, number>, name: string) => map.get(`wiki/${name}.md`);
    expect([of(first, 'a1'), of(first, 'b1')]).toEqual([0, 1]);

    // An Ingest grows the second community past the first, and a new community appears.
    const grown = [...small, 'b4', 'b5'];
    const fresh = ['c1', 'c2'];
    const next = model([...big, ...grown, ...fresh], [...clique(big), ...clique(grown), ...clique(fresh)]);
    expect(of(communityIndexes(next), 'b1')).toBe(0);
    const stable = communityIndexes(next, first);
    expect(new Set(big.map(name => of(stable, name)))).toEqual(new Set([0]));
    expect(new Set(grown.map(name => of(stable, name)))).toEqual(new Set([1]));
    expect(new Set(fresh.map(name => of(stable, name)))).toEqual(new Set([2]));

    // A filter that hides the first community does not hand its color to the second.
    const filtered = communityIndexes(model(grown, clique(grown)), stable);
    expect(new Set(grown.map(name => of(filtered, name)))).toEqual(new Set([1]));
  });
});
