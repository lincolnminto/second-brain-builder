import { afterEach, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrainContext } from '../../app/context';
import { brain } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import type { GraphModel } from '../graph/data';
import { GraphView } from '../graph/graph-view';
import { WikiPlaceholder } from './index';

vi.mock('../graph/graph-canvas', () => ({ default: ({ graph }: { graph: GraphModel }) =>
  <div data-testid="graph-nodes">{graph.nodes.map(node => <span key={node.path}>{node.type}: {node.title}</span>)}</div>,
}));
afterEach(() => vi.unstubAllGlobals());

const index = { path: 'wiki/name-indexes/book-a-z.md', title: 'Book: names a-z', type: 'name-index', description: null };
const context = { brainId: brain.id, brain, events: [], connected: true };

it('renders Name indexes as the final Wiki home group with a readable badge and page link', async () => {
  mockApi((_request, path) => path.endsWith('/pages') ? json([
    index, { path: 'wiki/entities/tree.md', title: 'Tree', type: 'entity', description: null },
  ]) : undefined);
  renderApp(<BrainContext.Provider value={context}><WikiPlaceholder /></BrainContext.Provider>);
  const section = await screen.findByRole('region', { name: 'Name indexes' });
  expect(within(section).getByText('Name index')).toHaveClass('type-name-index');
  expect(within(section).getByRole('link', { name: index.title })).toHaveAttribute('href', `/brains/${brain.id}/wiki/${index.path}`);
  expect(screen.getAllByRole('region').at(-1)).toBe(section);
});

it('renders name-index graph nodes, adds their color class to the legend, and filters them', async () => {
  mockApi((_request, path) => path.endsWith('/graph') ? json({
    nodes: [index, { path: 'sources/book/book.md', type: 'source', title: 'Book' }],
    edges: [{ from_path: index.path, to_path: 'sources/book/book.md' }],
  }) : undefined);
  renderApp(<BrainContext.Provider value={context}><GraphView brainId={brain.id} /></BrainContext.Provider>);
  expect(await screen.findByText(`name-index: ${index.title}`)).toBeInTheDocument();
  const legend = screen.getByRole('button', { name: 'Name index1' });
  expect(legend.querySelector('.knowledge-dot-name-index')).not.toBeNull();
  await userEvent.setup().click(legend);
  expect(legend).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByTestId('graph-nodes')).toHaveTextContent(`name-index: ${index.title}`);
  expect(screen.queryByText('source: Book')).not.toBeInTheDocument();
});
