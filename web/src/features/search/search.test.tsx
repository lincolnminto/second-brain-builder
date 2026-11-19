import { afterEach, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { BrainContext } from '../../app/context';
import { CommandPalette } from '../../app/command-palette';
import { brain } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { SearchPlaceholder } from './index';
import { searchResultUrl } from './results';

afterEach(() => vi.unstubAllGlobals());
function Location() { const location = useLocation(); return <div data-testid="location">{location.pathname}{location.search}</div>; }

it('submits the query and type to Brain search and renders safe snippets and encoded links', async () => {
  const requests = mockApi((_request, path) => path.endsWith('/search') ? json([{ path: 'wiki/concepts/field study.md', title: 'Field Study', type: 'concept', snippet: '**Attention** to detail. <script>bad()</script>', score: 1 }]) : undefined);
  const user = userEvent.setup();
  const { container } = renderApp(<BrainContext.Provider value={{ brainId: brain.id, brain, events: [], connected: true }}><SearchPlaceholder /></BrainContext.Provider>);
  await user.type(screen.getByLabelText('Search this Brain'), 'attention');
  await user.selectOptions(screen.getByLabelText('Page type'), 'concept');
  await user.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByRole('link', { name: 'Field Study' })).toHaveAttribute('href', '/brains/brain-1/wiki/wiki/concepts/field%20study.md');
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByText('Attention').tagName).toBe('STRONG');
  expect(requests.some(request => { const params = new URL(request.url).searchParams; return params.get('q') === 'attention' && params.getAll('type').includes('concept'); })).toBe(true);
});

it('offers full search from the command palette using the current query', async () => {
  mockApi();
  const user = userEvent.setup(); const close = vi.fn();
  renderApp(<><CommandPalette brainId={brain.id} open onOpenChange={close} /><Location /></>);
  await user.type(screen.getByRole('combobox'), 'wading');
  await user.click(await screen.findByRole('option', { name: 'See all results for “wading”' }));
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/brains/brain-1/search?q=wading'));
  expect(close).toHaveBeenCalledWith(false);
  expect(searchResultUrl('brain one', 'sources/Field Study/book.md')).toBe('/brains/brain%20one/wiki/sources/Field%20Study/book.md');
});
