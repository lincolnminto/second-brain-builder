import { describe, expect, it } from 'vitest';
import { emptyGraph, mergeSnapshot } from './data';
import { adjacency, emphasis, neighborhood, pageLinks, visibleGraph } from './selection';
import { edgeLook, nodeLook, type GraphPalette, type LookState } from './style';
import type { RenderNode } from './sync';

const page = (name: string, type = 'concept') => ({ path: `wiki/${name}.md`, title: name.toUpperCase(), type });
const edge = (from: string, to: string) => ({ from_path: `wiki/${from}.md`, to_path: `wiki/${to}.md` });
// a → b → c → d, a → e (an entity), and f alone.
const model = mergeSnapshot(emptyGraph(), {
  nodes: [page('a'), page('b'), page('c'), page('d'), page('e', 'entity'), page('f')],
  edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'd'), edge('a', 'e')],
});
const paths = (nodes: { path: string }[]) => nodes.map(node => node.path.slice(5, -3)).sort();

describe('neighborhoods', () => {
  it('walks links in both directions up to the given depth', () => {
    const neighbors = adjacency(model.links);
    expect(neighborhood(neighbors, 'wiki/c.md', 1)).toEqual(new Set(['wiki/c.md', 'wiki/b.md', 'wiki/d.md']));
    expect(neighborhood(neighbors, 'wiki/c.md', 2)).toEqual(new Set(['wiki/c.md', 'wiki/b.md', 'wiki/d.md', 'wiki/a.md']));
    expect(neighborhood(neighbors, 'wiki/f.md', 2)).toEqual(new Set(['wiki/f.md']));
  });

  it('shows only the neighbors of a page, at depth 1 or 2, and drops links that leave the scope', () => {
    const one = visibleGraph(model, { type: null, scope: { path: 'wiki/b.md', depth: 1 } });
    expect(one.scoped).toBe(true);
    expect(paths(one.nodes)).toEqual(['a', 'b', 'c']);
    expect(one.links).toEqual([edge('a', 'b'), edge('b', 'c')]);
    const two = visibleGraph(model, { type: null, scope: { path: 'wiki/b.md', depth: 2 } });
    expect(paths(two.nodes)).toEqual(['a', 'b', 'c', 'd', 'e']);
  });

  it('applies the type filter first and measures the scope over the links that stay visible', () => {
    const concepts = visibleGraph(model, { type: 'concept', scope: { path: 'wiki/a.md', depth: 2 } });
    expect(paths(concepts.nodes)).toEqual(['a', 'b', 'c']);
    // The entity e is filtered out, so it cannot be a neighbor.
    expect(concepts.links.every(link => link.to_path !== 'wiki/e.md')).toBe(true);
  });

  it('ignores a scope whose page is hidden or gone, and keeps the model node objects', () => {
    const hidden = visibleGraph(model, { type: 'entity', scope: { path: 'wiki/a.md', depth: 1 } });
    expect(hidden.scoped).toBe(false);
    expect(paths(hidden.nodes)).toEqual(['e']);
    const gone = visibleGraph(model, { type: null, scope: { path: 'wiki/missing.md', depth: 1 } });
    expect(gone.nodes).toBe(model.nodes);
    expect(gone.scoped).toBe(false);
  });

  it('lists outgoing and incoming links of a page by title', () => {
    const links = pageLinks(model, 'wiki/b.md');
    expect(paths(links.outgoing)).toEqual(['c']);
    expect(paths(links.incoming)).toEqual(['a']);
    expect(pageLinks(model, 'wiki/a.md').outgoing.map(node => node.title)).toEqual(['B', 'E']);
  });
});

describe('selection emphasis', () => {
  const palette: GraphPalette = {
    dark: false, background: '#f7f5f0', surface: '#ffffff', border: '#e7e2d8', foreground: '#1c1b19', muted: '#6b665c',
    types: { concept: '#5058a5', entity: '#16746b' },
  };
  const node = (overrides: Partial<RenderNode> = {}): RenderNode => ({ x: 0, y: 0, size: 10, label: 'A', type: 'concept', degree: 1, hub: false, fixed: false, ...overrides });
  const state = (selected: string | null, hovered: string | null = null): LookState => ({
    palette, colorBy: 'type', communities: new Map(), now: 0, reducedMotion: false,
    emphasis: emphasis(model.neighbors, selected, hovered),
  });

  it('highlights the selected page and its neighbors and fades the rest', () => {
    const selected = state('wiki/b.md');
    expect(selected.emphasis.related).toEqual(new Set(['wiki/a.md', 'wiki/b.md', 'wiki/c.md']));
    const self = nodeLook('wiki/b.md', node(), selected);
    expect(self).toMatchObject({ opacity: 1, labelVisibility: 'visible', backdropVisibility: 'visible', coreColor: '#5058a5', depth: 'topNodes' });
    expect(self.size).toBeGreaterThan(10);
    expect(nodeLook('wiki/c.md', node(), selected)).toMatchObject({ opacity: 1, labelVisibility: 'visible', backdropVisibility: 'hidden' });
    expect(nodeLook('wiki/d.md', node({ hub: true }), selected)).toMatchObject({ opacity: 0.16, labelVisibility: 'hidden', backdropVisibility: 'hidden' });
    expect(edgeLook('wiki/a.md', 'wiki/b.md', selected).depth).toBe('topEdges');
    expect(edgeLook('wiki/c.md', 'wiki/d.md', selected).opacity).toBeLessThan(0.1);
  });

  it('lets the selection win over the hovered page, which still shows its label pill', () => {
    const both = state('wiki/b.md', 'wiki/f.md');
    expect(both.emphasis.active).toBe('wiki/b.md');
    expect(nodeLook('wiki/f.md', node(), both)).toMatchObject({ opacity: 1, labelVisibility: 'visible', backdropVisibility: 'visible' });
    expect(nodeLook('wiki/d.md', node(), both).opacity).toBe(0.16);
    const hoverOnly = state(null, 'wiki/f.md');
    expect(nodeLook('wiki/f.md', node(), hoverOnly)).toMatchObject({ opacity: 1, labelVisibility: 'visible', backdropVisibility: 'visible' });
    expect(nodeLook('wiki/a.md', node(), hoverOnly).opacity).toBe(0.16);
  });

  it('without an active page, shows every page and pills only for hubs', () => {
    const idle = state(null);
    expect(nodeLook('wiki/a.md', node(), idle)).toMatchObject({ opacity: 1, labelVisibility: 'auto', backdropVisibility: 'hidden' });
    expect(nodeLook('wiki/a.md', node({ hub: true }), idle)).toMatchObject({ labelVisibility: 'visible', backdropVisibility: 'visible' });
    expect(edgeLook('wiki/a.md', 'wiki/b.md', idle)).toMatchObject({ depth: 'edges', size: 1 });
  });

  it('colors rings by type, or by community with a muted color for pages without one', () => {
    const idle = state(null);
    expect(nodeLook('wiki/e.md', node({ type: 'entity' }), idle).color).toBe('#16746b');
    expect(nodeLook('wiki/e.md', node({ type: 'unknown' }), idle).color).toBe(palette.muted);
    const communities: LookState = { ...idle, colorBy: 'community', communities: new Map([['wiki/a.md', 0], ['wiki/f.md', -1]]) };
    expect(nodeLook('wiki/a.md', node(), communities).color).not.toBe(nodeLook('wiki/a.md', node(), idle).color);
    expect(nodeLook('wiki/f.md', node(), communities).color).toBe(palette.muted);
  });

  it('pulses a page after a live write, unless motion is reduced', () => {
    const live = { ...state(null), now: 450 };
    expect(nodeLook('wiki/a.md', node({ pulseAt: 0 }), live).size).toBeGreaterThan(10);
    expect(nodeLook('wiki/a.md', node({ pulseAt: 0 }), { ...live, reducedMotion: true }).size).toBe(10);
    expect(nodeLook('wiki/a.md', node({ pulseAt: 0 }), { ...live, now: 5000 }).size).toBe(10);
  });
});
