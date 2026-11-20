import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { BrainContext } from '../../app/context';
import { brain } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import type { GraphSubset } from './data';
import type { FocusRequest } from './graph-canvas';
import { GraphView } from './graph-view';

type CanvasProps = {
  graph: GraphSubset; selected: string; pinned: ReadonlySet<string>; focusRequest: FocusRequest | null;
  onOpen: (path: string) => void; onSelect: (path: string) => void; onContextMenu: (path: string, point: { x: number; y: number }) => void;
};
// The real canvas needs WebGL. This one lists the pages it gets and forwards
// clicks; like the real one, it is a focusable region.
vi.mock('./graph-canvas', () => ({
  default: ({ graph, selected, pinned, focusRequest, onOpen, onSelect, onContextMenu }: CanvasProps) => <div data-testid="canvas" role="region" tabIndex={0} aria-label="Graph canvas">
    {graph.nodes.map(node => <button key={node.path} type="button" data-testid={`node-${node.title}`}
      onClick={() => onSelect(node.path)} onDoubleClick={() => onOpen(node.path)} onContextMenu={event => {
        // Like the renderer: a right click selects the page, then opens its menu.
        event.preventDefault();
        onSelect(node.path);
        onContextMenu(node.path, { x: 20, y: 20 });
      }}>
      {node.title}{pinned.has(node.path) ? ' (pinned)' : ''}
    </button>)}
    <output data-testid="selected">{selected}</output>
    <output data-testid="focus">{focusRequest?.path ?? ''}</output>
  </div>,
}));
afterEach(() => vi.unstubAllGlobals());

const node = (name: string, type: string, path = `wiki/${type}/${name.toLowerCase()}.md`) => ({ path, title: name, type });
const dune = node('Dune', 'source', 'sources/books/dune/dune.md');
const summary = node('Dune summary', 'summary');
const paul = node('Paul', 'entity');
const spice = node('Spice', 'concept');
const fremen = node('Fremen', 'entity');
const work = node('Work notes', 'source', 'sources/work/notes/notes.md');
const link = (from: { path: string }, to: { path: string }) => ({ from_path: from.path, to_path: to.path });
const fullGraph = {
  nodes: [dune, summary, paul, spice, fremen],
  // Paul links back to the summary: one connection, drawn as one edge.
  edges: [link(summary, dune), link(summary, paul), link(paul, summary), link(paul, spice), link(spice, fremen)],
};
const folders = {
  name: '', path: '', source_count: 0, total_source_count: 2, children: [
    { name: 'books', path: 'books', source_count: 1, total_source_count: 1, children: [] },
    { name: 'work', path: 'work', source_count: 1, total_source_count: 1, children: [] },
  ],
};
const context = { brainId: brain.id, brain, events: [], connected: true };

function renderGraph() {
  const requests = mockApi((request, path) => {
    if (path.endsWith('/graph')) {
      const folder = new URL(request.url).searchParams.get('folder');
      return json(folder === 'work' ? { nodes: [work], edges: [] } : fullGraph);
    }
    if (path.endsWith('/folders')) return json(folders);
    if (path.includes('/pages/')) return json({ ...paul, description: 'A young duke.', tags: ['house-atreides'], sources: [], generated_by: null, generated_at: null, body: '' });
    if (path.endsWith('/pages')) return json([{ ...dune, description: 'A desert planet novel.', tags: [] }]);
    return undefined;
  });
  renderApp(<BrainContext.Provider value={context}><GraphView brainId={brain.id} /><CurrentPath /></BrainContext.Provider>, `/brains/${brain.id}/graph`);
  return requests;
}
function CurrentPath() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}
const canvas = () => screen.findByTestId('canvas');
const shownTitles = () => within(screen.getByTestId('canvas')).getAllByRole('button').map(button => button.textContent);

describe('graph filters', () => {
  it('shows only page types that have pages, and All types reveals the empty ones, disabled, with a reason', async () => {
    renderGraph();
    await canvas();
    // Paul → summary and summary → Paul are one connection, as the canvas draws them.
    expect(document.querySelector('.knowledge-counts')).toHaveTextContent('5 pages/4 connections');
    const legend = screen.getByRole('group', { name: 'Filter by page type' });
    const names = () => within(legend).getAllByRole('button').map(button => button.textContent);
    expect(names()).toEqual(['All pages', 'Source1', 'Summary1', 'Entity2', 'Concept1', 'All types']);

    const user = userEvent.setup();
    await user.click(within(legend).getByRole('button', { name: 'All types' }));
    expect(within(legend).getByRole('button', { name: 'All types' })).toHaveAttribute('aria-pressed', 'true');
    const comparison = within(legend).getByRole('button', { name: 'Comparison0' });
    expect(comparison).toHaveAttribute('aria-disabled', 'true');
    expect(comparison).toHaveAccessibleDescription('Ingest writes comparison pages when a Source compares things');
    expect(within(legend).getByRole('button', { name: 'Synthesis0' })).toHaveAccessibleDescription('Synthesis pages come from answers saved by you or your agents');
    expect(within(legend).getByRole('button', { name: 'Name index0' })).toHaveAccessibleDescription('Name Index pages list minor names of large Sources');
    await user.click(comparison);
    expect(comparison).toHaveAttribute('aria-pressed', 'false');
    expect(shownTitles()).toHaveLength(5);

    await user.click(within(legend).getByRole('button', { name: 'Entity2' }));
    expect(shownTitles()).toEqual(['Paul', 'Fremen']);
    expect(within(legend).getByRole('button', { name: 'Entity2' })).not.toHaveAccessibleDescription();
  });

  it('filters by Folder through the graph endpoint and clears the Folder filter', async () => {
    const requests = renderGraph();
    await canvas();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole('option', { name: 'work' })).toBeInTheDocument());
    await user.selectOptions(screen.getByRole('combobox', { name: 'Folder' }), 'work');
    await waitFor(() => expect(shownTitles()).toEqual(['Work notes']));
    expect(requests.some(request => new URL(request.url).searchParams.get('folder') === 'work')).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Show all Folders' }));
    await waitFor(() => expect(shownTitles()).toHaveLength(5));
  });
});

describe('selection, inspector, and node menu', () => {
  it('inspects the selected page with its description, tags, and links in and out', async () => {
    renderGraph();
    await canvas();
    const user = userEvent.setup();
    await user.click(screen.getByTestId('node-Paul'));
    const inspector = screen.getByRole('complementary', { name: 'Selected page' });
    expect(within(inspector).getByRole('heading', { name: 'Paul' })).toBeInTheDocument();
    expect(await within(inspector).findByText('A young duke.')).toBeInTheDocument();
    expect(within(inspector).getByText('house-atreides')).toBeInTheDocument();
    expect(within(inspector).getByText('2 connections')).toBeInTheDocument();
    expect(within(inspector).getByRole('link', { name: 'Open Wiki Page' })).toHaveAttribute('href', `/brains/${brain.id}/wiki/${paul.path}`);

    // A link to a page that a filter hides clears the filter and selects it.
    await user.click(screen.getByRole('button', { name: 'Entity2' }));
    await user.click(within(inspector).getByRole('button', { name: 'Spice' }));
    expect(screen.getByTestId('selected')).toHaveTextContent(spice.path);
    expect(screen.getByTestId('focus')).toHaveTextContent(spice.path);
    expect(shownTitles()).toHaveLength(5);

    await user.click(within(screen.getByRole('complementary', { name: 'Selected page' })).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('complementary', { name: 'Selected page' })).not.toBeInTheDocument();
  });

  it('opens a Canonical Document in its Source view, never in the Wiki, and a Wiki Page in the Wiki', async () => {
    const requests = renderGraph();
    await canvas();
    const user = userEvent.setup();
    await user.dblClick(screen.getByTestId('node-Dune'));
    expect(screen.getByTestId('location')).toHaveTextContent(`/brains/${brain.id}/sources/dune`);
    await user.dblClick(screen.getByTestId('node-Paul'));
    expect(screen.getByTestId('location')).toHaveTextContent(`/brains/${brain.id}/wiki/${paul.path}`);

    await user.click(screen.getByTestId('node-Dune'));
    const inspector = screen.getByRole('complementary', { name: 'Selected page' });
    expect(await within(inspector).findByText('A desert planet novel.')).toBeInTheDocument();
    expect(within(inspector).getByRole('link', { name: 'Open Source' })).toHaveAttribute('href', `/brains/${brain.id}/sources/dune`);
    expect(within(inspector).queryByRole('link', { name: 'Open Wiki Page' })).not.toBeInTheDocument();
    // The full Canonical Document (it can be several MB) is never fetched.
    expect(requests.some(request => new URL(request.url).pathname.includes(`/pages/${dune.path}`))).toBe(false);
  });

  it('moves focus to the newly selected page after a link, and back to where the selection was made on close', async () => {
    renderGraph();
    await canvas();
    const user = userEvent.setup();
    const inspector = () => screen.getByRole('complementary', { name: 'Selected page' });
    await user.click(screen.getByTestId('node-Paul'));
    within(inspector()).getByRole('button', { name: 'Spice' }).focus();
    await user.keyboard('{Enter}');
    expect(within(inspector()).getByRole('heading', { name: 'Spice' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('complementary', { name: 'Selected page' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Graph canvas' })).toHaveFocus();

    const picker = screen.getByRole('combobox', { name: 'Find a page in the graph' });
    await user.selectOptions(picker, paul.path);
    await user.click(within(inspector()).getByRole('button', { name: 'Close' }));
    expect(picker).toHaveFocus();
  });

  it('shows only neighbors at depth 1 or 2 with a chip to show all again', async () => {
    renderGraph();
    await canvas();
    const user = userEvent.setup();
    await user.pointer({ keys: '[MouseRight]', target: screen.getByTestId('node-Paul') });
    const menu = screen.getByRole('menu', { name: 'Actions for Paul' });
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent))
      .toEqual(['Open page', 'Focus neighborhood', 'Show only neighbors', 'Show neighbors two steps away', 'Pin position', 'Copy link']);
    expect(within(menu).getAllByRole('menuitem')[0]).toHaveFocus();
    await user.click(within(menu).getByRole('menuitem', { name: 'Show only neighbors' }));
    expect(shownTitles()).toEqual(['Dune summary', 'Paul', 'Spice']);
    expect(screen.getByText('Neighbors of Paul')).toBeInTheDocument();

    await user.pointer({ keys: '[MouseRight]', target: screen.getByTestId('node-Paul') });
    await user.click(screen.getByRole('menuitem', { name: 'Show neighbors two steps away' }));
    expect(shownTitles()).toEqual(['Dune', 'Dune summary', 'Paul', 'Spice', 'Fremen']);
    expect(screen.getByText('Two steps from Paul')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show all' }));
    expect(screen.queryByText(/Two steps from/)).not.toBeInTheDocument();
    expect(shownTitles()).toHaveLength(5);
  });

  it('pins, copies a link, opens Canonical Documents as Sources, and closes with Escape', async () => {
    renderGraph();
    await canvas();
    // user-event replaces the clipboard with a stub that records writes.
    const user = userEvent.setup();
    await user.pointer({ keys: '[MouseRight]', target: screen.getByTestId('node-Paul') });
    await user.click(screen.getByRole('menuitem', { name: 'Pin position' }));
    expect(screen.getByTestId('node-Paul')).toHaveTextContent('Paul (pinned)');
    expect(within(screen.getByRole('complementary', { name: 'Selected page' })).getByText('Pinned')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Actions' }));
    expect(screen.getByRole('menuitem', { name: 'Unpin position' })).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: 'Copy link' }));
    const copied = await screen.findByText('Link copied.');
    expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}/brains/${brain.id}/wiki/${paul.path}`);

    // A Canonical Document opens, and links, to its Source view.
    await user.pointer({ keys: '[MouseRight]', target: screen.getByTestId('node-Dune') });
    const menu = screen.getByRole('menu', { name: 'Actions for Dune' });
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent))
      .toEqual(['Open Source', 'Focus neighborhood', 'Show only neighbors', 'Show neighbors two steps away', 'Pin position', 'Copy link']);
    expect(within(menu).getByRole('menuitem', { name: 'Open Source' })).toHaveFocus();
    await user.click(within(menu).getByRole('menuitem', { name: 'Copy link' }));
    expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}/brains/${brain.id}/sources/dune`);
    // The second copy replaces the status text, so screen readers announce it again.
    await waitFor(() => expect(screen.getByText('Link copied.')).not.toBe(copied));

    await user.pointer({ keys: '[MouseRight]', target: screen.getByTestId('node-Dune') });
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('menuitem', { name: 'Copy link' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
