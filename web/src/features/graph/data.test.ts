import { describe, expect, it } from 'vitest';
import type { Schema } from '../../api/client';
import { applyPageEvent, emptyGraph, filterGraph, mergeSnapshot, wikiPageUrl } from './data';

const a = { path: 'wiki/concepts/a.md', title: 'Attention', type: 'concept' };
const b = { path: 'wiki/entities/b.md', title: 'Birds', type: 'entity' };
const c = { path: 'wiki/summaries/c.md', title: 'Coastal birds', type: 'summary' };
const edge = (from: string, to: string) => ({ from_path: from, to_path: to });
const snapshot: Schema['GraphView'] = { nodes: [a, b], edges: [edge(a.path, b.path)] };

function event(kind: string, data: Record<string, unknown>, id = 1): Schema['JobEventView'] {
  return { id, job_id: 1, brain_id: 'brain-1', created_at: '2026-11-19T12:00:00Z', kind, data };
}
const write = (page: typeof a, links: unknown[], id = 1) => event('page_written', { ...page, links }, id);

describe('knowledge graph data', () => {
  it('loads a snapshot with unique nodes and directed edges, excluding self-links and unresolved endpoints from the simulation', () => {
    const graph = mergeSnapshot(emptyGraph(), {
      nodes: [a, b, a],
      edges: [edge(a.path, b.path), edge(a.path, b.path), edge(b.path, a.path), edge(a.path, a.path), edge(b.path, c.path)],
    });
    expect(graph.nodes.map(node => node.path)).toEqual([a.path, b.path]);
    expect(graph.links).toHaveLength(2);
    expect(graph.edges).toHaveLength(3);
    // Reciprocal references are two directed edges, but one neighbor.
    expect(graph.nodes.map(node => node.degree)).toEqual([1, 1]);
    expect(graph.neighbors.get(a.path)).toEqual(new Set([b.path]));
    expect(snapshot).toEqual({ nodes: [a, b], edges: [edge(a.path, b.path)] });
    expect(a).not.toHaveProperty('degree');
  });

  it('preserves object identity, coordinates, velocity, and active pulses on snapshot reconciliation', () => {
    const initial = mergeSnapshot(emptyGraph(), snapshot);
    Object.assign(initial.nodes[0]!, { x: 32, y: -18, vx: 0.25, vy: -0.1, pulseAt: 50 });
    const graph = mergeSnapshot(initial, { nodes: [{ ...a, title: 'Updated' }, b, c], edges: [edge(a.path, c.path)] });
    expect(graph.nodes[0]).toBe(initial.nodes[0]);
    expect(graph.nodes[0]).toMatchObject({ title: 'Updated', x: 32, y: -18, vx: 0.25, vy: -0.1, pulseAt: 50 });
    expect(graph.links).toHaveLength(1);
    expect(graph.links[0]).toMatchObject(edge(a.path, c.path));
    expect(graph.nodes[1]!.degree).toBe(0);
    expect(mergeSnapshot(graph, { nodes: [c], edges: [] }).nodes.map(node => node.path)).toEqual([c.path]);
  });

  it('adds and updates pages, replaces outgoing links, retains incoming links, and deduplicates repeated writes', () => {
    let graph = mergeSnapshot(emptyGraph(), snapshot);
    const existing = graph.nodes[1]!;
    Object.assign(graph.nodes[0]!, { x: 0, y: 0 });
    Object.assign(existing, { x: 10, y: 20 });
    graph = applyPageEvent(graph, write(c, [a.path, a.path]), 100);
    expect(graph.nodes).toHaveLength(3);
    expect(graph.links).toHaveLength(2);
    expect(graph.nodes[2]).toMatchObject({ x: 14, y: 14, pulseAt: 100 });
    const updated = { ...b, title: 'Wading birds', type: 'concept' };
    graph = applyPageEvent(graph, write(updated, [c.path, c.path, b.path]), 200);
    graph = applyPageEvent(graph, write(updated, [c.path]), 220);
    expect(graph.nodes).toHaveLength(3);
    expect(graph.nodes[1]).toBe(existing);
    expect(existing).toMatchObject({ title: 'Wading birds', type: 'concept', x: 10, y: 20, pulseAt: 220, degree: 2 });
    expect(graph.edges).toEqual([edge(a.path, b.path), edge(c.path, a.path), edge(b.path, c.path)]);
    graph = applyPageEvent(graph, write(updated, []), 300);
    expect(graph.edges).toEqual([edge(a.path, b.path), edge(c.path, a.path)]);
  });

  it('waits for unknown linked pages and connects them when they arrive, including across a refetch', () => {
    let graph = mergeSnapshot(emptyGraph(), { nodes: [a], edges: [] });
    Object.assign(graph.nodes[0]!, { x: 40, y: 50 });
    graph = applyPageEvent(graph, write(a, [c.path, c.path, a.path, null, 12]), 100);
    expect(graph.links).toEqual([]);
    expect(graph.edges).toEqual([edge(a.path, c.path)]);
    expect(graph.nodes[0]!.degree).toBe(0);
    graph = mergeSnapshot(graph, { nodes: [a], edges: [] });
    graph = applyPageEvent(graph, write(c, []), 200);
    expect(graph.links).toHaveLength(1);
    expect(graph.links[0]).toMatchObject(edge(a.path, c.path));
    expect(graph.nodes[1]).toMatchObject({ x: 54, y: 64, degree: 1, pulseAt: 200 });
  });

  it('removes a deleted page and all incident edges, including pending links, and recomputes degrees', () => {
    let graph = mergeSnapshot(emptyGraph(), snapshot);
    graph = applyPageEvent(graph, write(b, [a.path, c.path]), 100);
    graph = applyPageEvent(graph, event('page_deleted', { path: b.path }), 200);
    expect(graph.nodes.map(node => node.path)).toEqual([a.path]);
    expect(graph.edges).toEqual([]);
    expect(graph.links).toEqual([]);
    expect(graph.nodes[0]!.degree).toBe(0);
    graph = applyPageEvent(graph, write(c, []), 300);
    expect(graph.links).toEqual([]);
    expect(applyPageEvent(graph, event('page_deleted', { path: 'unknown.md' }), 400).nodes).toEqual(graph.nodes);
  });

  it('keeps canonical edges when the force engine replaces link endpoints with node objects', () => {
    const graph = mergeSnapshot(emptyGraph(), snapshot);
    graph.links[0]!.source = graph.nodes[0]!;
    graph.links[0]!.target = graph.nodes[1]!;
    const next = applyPageEvent(graph, write(c, [b.path]), 100);
    expect(next.links[0]).toMatchObject({ source: a.path, target: b.path });
    expect(next.edges).toEqual([edge(a.path, b.path), edge(c.path, b.path)]);
  });

  it('ignores unrelated or malformed events', () => {
    const graph = mergeSnapshot(emptyGraph(), snapshot);
    for (const input of [event('progress', { path: a.path }), event('page_written', { ...a }), event('page_written', { ...a, links: 'bad' }), event('page_deleted', { path: 7 }), event('page_deleted', { path: '' })]) {
      expect(applyPageEvent(graph, input, 100)).toBe(graph);
    }
  });

  it('filters both endpoints without changing the original layout or full-graph degrees', () => {
    const graph = mergeSnapshot(emptyGraph(), { nodes: [a, b, c], edges: [edge(a.path, b.path), edge(b.path, c.path)] });
    expect(filterGraph(graph, null)).toEqual({ nodes: graph.nodes, links: graph.links });
    const filtered = filterGraph(graph, 'entity');
    expect(filtered.nodes).toEqual([graph.nodes[1]]);
    expect(filtered.nodes[0]).toBe(graph.nodes[1]);
    expect(filtered.nodes[0]!.degree).toBe(2);
    expect(filtered.links).toEqual([]);
    expect(filterGraph(graph, 'synthesis')).toEqual({ nodes: [], links: [] });
  });

  it('merges a 2,000-page snapshot and incremental events without duplicating pages or resetting positions', () => {
    const pages = Array.from({ length: 2000 }, (_, index) => ({ path: `wiki/concepts/${index}.md`, title: `Page ${index}`, type: 'concept' }));
    const graph = mergeSnapshot(emptyGraph(), { nodes: pages, edges: pages.slice(1).map(page => edge(pages[0]!.path, page.path)) });
    Object.assign(graph.nodes[500]!, { x: 80, y: 90 });
    const next = applyPageEvent(graph, write(pages[1000]!, [pages[500]!.path, pages[500]!.path]), 100);
    expect(next.nodes).toHaveLength(2000);
    expect(next.links).toHaveLength(2000);
    expect(next.nodes[500]).toBe(graph.nodes[500]);
    expect(next.nodes[500]).toMatchObject({ x: 80, y: 90, degree: 2 });
  });

  it('encodes the Brain id and each full Brain-relative path segment for the Wiki route', () => {
    expect(wikiPageUrl('brain /1', 'wiki/concepts/a #b?.md')).toBe('/brains/brain%20%2F1/wiki/wiki/concepts/a%20%23b%3F.md');
  });
});
