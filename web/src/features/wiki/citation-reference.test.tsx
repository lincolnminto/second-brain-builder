import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router';
import type { Schema } from '../../api/client';
import { BrainContext } from '../../app/context';
import { brain, event } from '../../test/fixtures';
import { json, mockApi, renderApp } from '../../test/helpers';
import { WikiPlaceholder } from './index';
import { WikiMarkdown } from './wiki-markdown';

afterEach(() => vi.unstubAllGlobals());

// The page of the server's cited-sentence tests: six references, the last one in a footnote definition.
const page: Schema['PageView'] = {
  path: 'wiki/entities/grey-heron.md', title: 'Grey Heron', type: 'entity', description: null, tags: [], generated_by: null, generated_at: null,
  sources: [
    { id: 'shore', resource: '/sources/books/shore/shore.md', title: 'Shore Book' },
    { id: 'tides', resource: '/sources/nature/tides/tides.md', title: 'Tides' },
    { id: 'birds', resource: '/sources/nature/birds/birds.md', title: 'Coastal Birds' },
  ],
  body: [
    'The grey heron waits for fish (Shore Book 11:1-9).[^shore] It wades into pools at low',
    'tide.[^tides] The heron[^birds] nests in tall trees (see chapter 3) near the coast.',
    '',
    'A later paragraph (cf. Gen. 2:4) cites twice.[^shore][^tides] An unknown label[^none] is text. Code `[^shore]` is text.',
    '',
    '[^shore]: [Shore Book](/sources/books/shore/shore.md), and see[^birds].',
    '[^tides]: [Tides](/sources/nature/tides/tides.md)',
    '[^birds]: Coastal Birds, without a link.',
  ].join('\n'),
};
const passage: Schema['CitationPassage'] = {
  source_id: 'shore', source_path: 'sources/books/shore/shore.md', source_title: 'Shore Book', char_start: 120, char_end: 245,
  heading_trail: ['Shore Book', 'Part One', 'Herons'], text: 'The grey heron waits by the <b>shore</b> for fish at low tide.\n<img src=x onerror=alert(1)>',
  score: 0.8, method: 'terms',
};
const label = (title: string) => `Open the cited passage in ${title}`;
const citations = (requests: Request[]) => requests.map(request => new URL(request.url)).filter(url => url.pathname.endsWith('/citations/resolve'));
function marks(card: HTMLElement) { return [...card.querySelectorAll('mark')].map(mark => mark.textContent); }

it('numbers footnote references as the citations API does: the body first, then footnote definitions', async () => {
  // The server answers with a passage of the Source that each reference cites.
  const cited = ['shore', 'tides', 'birds', 'shore', 'tides', 'birds'];
  const requests = mockApi((request, path) => path === '/api/brains/brain-1/citations/resolve'
    ? json({ ...passage, source_id: cited[Number(new URL(request.url).searchParams.get('ref'))] }) : undefined);
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const references = screen.getAllByRole('link', { name: /^Open the cited passage in / });
  expect(references.map(reference => reference.getAttribute('aria-label'))).toEqual(
    ['Shore Book', 'Tides', 'Coastal Birds', 'Shore Book', 'Tides', 'Coastal Birds'].map(label));
  expect(container.querySelector('section[data-footnotes]')?.contains(references[5]!)).toBe(true);
  expect(screen.getByText(/An unknown label\[\^none\] is text/)).toBeInTheDocument();
  for (const [index, reference] of references.entries()) {
    act(() => reference.focus());
    await waitFor(() => expect(citations(requests).at(-1)?.searchParams.get('ref')).toBe(String(index)));
    expect(citations(requests).at(-1)?.searchParams.get('page')).toBe(page.path);
  }
  // Each reference highlights the words of its own sentence in the passage.
  for (const [index, words] of [[0, ['grey', 'heron', 'waits', 'shore', 'fish']], [1, ['low', 'tide']], [2, ['heron']]] as const) {
    act(() => references[index]!.focus());
    await waitFor(() => expect(marks(container.querySelector('.wiki-citation-card') as HTMLElement)).toEqual(words));
  }
});

it('shows the loading, found, and not-found states of the passage as plain text', async () => {
  const user = userEvent.setup();
  let release: (response: Response) => void = () => undefined;
  const requests = mockApi((request, path) => {
    if (path !== '/api/brains/brain-1/citations/resolve') return undefined;
    const ref = new URL(request.url).searchParams.get('ref');
    if (ref === '0') return new Promise<Response>(resolve => { release = resolve; });
    return json({ code: 'not_found', message: 'No passage matches this citation.' }, 404);
  });
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const [first, second] = screen.getAllByRole('link', { name: /^Open the cited passage in / });
  expect(first).toHaveAttribute('href', '/brains/brain-1/sources/shore');

  await user.tab();
  expect(first).toHaveFocus();
  expect(await screen.findByText('Finding the cited passage…')).toBeInTheDocument();
  await waitFor(() => expect(citations(requests)).toHaveLength(1));
  act(() => release(json(passage)));
  const card = container.querySelector('.wiki-citation-card') as HTMLElement;
  await waitFor(() => expect(within(card).getByRole('link', { name: 'Open in document' })).toHaveAttribute('href', '/brains/brain-1/sources/shore#passage=120-245'));
  expect(first).toHaveAttribute('href', '/brains/brain-1/sources/shore#passage=120-245');
  expect(first).toHaveAccessibleDescription(/The grey heron waits by the <b>shore<\/b> for fish/);
  expect(within(card).getByText('Part One › Herons')).toBeInTheDocument();
  expect(marks(card)).toEqual(['grey', 'heron', 'waits', 'shore', 'fish']);
  expect(card.querySelector('b, img')).toBeNull();
  // Tab reaches the link of the card; Escape closes the card and returns to the reference.
  await user.tab();
  expect(within(card).getByRole('link', { name: 'Open in document' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(container.querySelector('.wiki-citation-card')).toBeNull();
  expect(first).toHaveFocus();

  await user.hover(second!);
  expect(await screen.findByText('No passage of this Canonical Document matches the citation.')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Open in document' })).toHaveAttribute('href', '/brains/brain-1/sources/tides');
  // Escape closes a card that hover opened while the focus is on another element, and leaves the focus there.
  await user.keyboard('{Escape}');
  expect(container.querySelector('.wiki-citation-card')).toBeNull();
  expect(first).toHaveFocus();
  await user.unhover(second!);
  await user.hover(second!);
  expect(await screen.findByText('No passage of this Canonical Document matches the citation.')).toBeInTheDocument();
  await user.unhover(second!);
  await waitFor(() => expect(container.querySelector('.wiki-citation-card')).toBeNull());
});

it('counts a reference after a bare URL, as the citations API does', async () => {
  const requests = mockApi((_request, path) => path === '/api/brains/brain-1/citations/resolve' ? json(passage) : undefined);
  const urls: Schema['PageView'] = { ...page, body: [
    'The archive is at https://example.com/archive.[^shore] It holds maps.[^tides]',
    '',
    'See https://example.com/a/[^birds]/rest, (https://example.com/b)[^shore], and www.example.com.[^TIDES] An escaped https://example.com/c\\[^shore] is text.',
    '',
    '[^shore]: Shore Book',
    '[^tides]: Tides',
    '[^birds]: Coastal Birds',
  ].join('\n') };
  renderApp(<WikiMarkdown brainId="brain-1" page={urls} />);
  // The URLs stay links, without the reference or the punctuation after them.
  expect(screen.getByRole('link', { name: 'https://example.com/archive' })).toHaveAttribute('href', 'https://example.com/archive');
  expect(screen.getByRole('link', { name: 'https://example.com/a/' })).toHaveAttribute('href', 'https://example.com/a/');
  expect(screen.getByRole('link', { name: 'https://example.com/b' })).toHaveAttribute('href', 'https://example.com/b');
  expect(screen.getByRole('link', { name: 'www.example.com' })).toHaveAttribute('href', 'http://www.example.com');
  expect(screen.getByText(/See/)).toHaveTextContent(/^See https:\/\/example\.com\/a\/\d+\/rest, \(https:\/\/example\.com\/b\)\d+, and www\.example\.com\.\d+ An escaped/);
  // search.cited_sentence finds five references in this body: shore, tides, birds, shore, TIDES.
  const references = screen.getAllByRole('link', { name: /^Open the cited passage in / });
  expect(references.map(reference => reference.getAttribute('aria-label'))).toEqual(
    ['Shore Book', 'Tides', 'Coastal Birds', 'Shore Book', 'Tides'].map(label));
  for (const [index, reference] of references.entries()) {
    act(() => reference.focus());
    await waitFor(() => expect(citations(requests).at(-1)?.searchParams.get('ref')).toBe(String(index)));
  }
});

it('does not show a passage of another Source than the cited one', async () => {
  // A passage of "tides" for a reference to "shore" means that the page and the server count references in another way.
  mockApi((_request, path) => path === '/api/brains/brain-1/citations/resolve' ? json({ ...passage, source_id: 'tides', source_title: 'Tides' }) : undefined);
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const [first] = screen.getAllByRole('link', { name: label('Shore Book') });
  act(() => first!.focus());
  expect(await screen.findByText('No passage of this Canonical Document matches the citation.')).toBeInTheDocument();
  const card = container.querySelector('.wiki-citation-card') as HTMLElement;
  expect(card.querySelector('.wiki-citation-passage')).toBeNull();
  expect(within(card).getByText('Shore Book')).toBeInTheDocument();
  expect(within(card).getByRole('link', { name: 'Open in document' })).toHaveAttribute('href', '/brains/brain-1/sources/shore');
  expect(first).toHaveAttribute('href', '/brains/brain-1/sources/shore');
});

it('keeps an open card and the focus in it when a job event renders the Wiki Page view again', async () => {
  const user = userEvent.setup();
  mockApi((_request, path) => {
    if (path === '/api/brains/brain-1/citations/resolve') return json(passage);
    if (path.startsWith('/api/brains/brain-1/pages/')) return json(page);
    return undefined;
  });
  let addEvent = () => undefined as void;
  function Shell() {
    const [events, setEvents] = useState<Schema['JobEventView'][]>([]);
    addEvent = () => setEvents(previous => [...previous, { ...event, id: previous.length + 1 }]);
    // As the app shell does: a new context value on each event.
    return <BrainContext.Provider value={{ brainId: brain.id, brain, events, connected: true }}>
      <Routes><Route path="/brains/:brainId/wiki/*" element={<WikiPlaceholder />} /></Routes>
    </BrainContext.Provider>;
  }
  const { container } = renderApp(<Shell />, `/brains/${brain.id}/wiki/${page.path}`);
  const [first] = await screen.findAllByRole('link', { name: label('Shore Book') });
  act(() => first!.focus());
  const open = await screen.findByRole('link', { name: 'Open in document' });
  await waitFor(() => expect(open).toHaveAttribute('href', '/brains/brain-1/sources/shore#passage=120-245'));
  await user.tab();
  expect(open).toHaveFocus();
  act(() => addEvent());
  act(() => addEvent());
  expect(first).toBeInTheDocument();
  expect(open).toBeInTheDocument();
  expect(open).toHaveFocus();
  expect(container.querySelectorAll('.wiki-citation-card')).toHaveLength(1);
});

it('moves the focus to the card link when Try again loads the passage', async () => {
  const user = userEvent.setup();
  let failures = 1;
  mockApi((_request, path) => {
    if (path !== '/api/brains/brain-1/citations/resolve') return undefined;
    return failures-- > 0 ? json({ code: 'internal', message: 'The server failed.' }, 500) : json(passage);
  });
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const [first] = screen.getAllByRole('link', { name: /^Open the cited passage in / });
  await user.tab();
  expect(first).toHaveFocus();
  const retry = await screen.findByRole('button', { name: 'Try again' });
  expect(screen.getByText('The cited passage could not be loaded.')).toBeInTheDocument();
  await user.tab();
  expect(retry).toHaveFocus();
  await user.keyboard('{Enter}');
  const card = container.querySelector('.wiki-citation-card') as HTMLElement;
  await waitFor(() => expect(card.querySelector('.wiki-citation-passage')).not.toBeNull());
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  await waitFor(() => expect(within(card).getByRole('link', { name: 'Open in document' })).toHaveFocus());
  await user.keyboard('{Escape}');
  expect(container.querySelector('.wiki-citation-card')).toBeNull();
  expect(first).toHaveFocus();
});

it('opens the Source view at the passage when a reference is clicked', async () => {
  mockApi((_request, path) => path === '/api/brains/brain-1/citations/resolve' ? json(passage) : undefined);
  function SourceRoute() { const { pathname, hash } = useLocation(); return <p>Source view {pathname}{hash}</p>; }
  renderApp(<Routes>
    <Route path="/brains/:brainId/wiki/*" element={<WikiMarkdown brainId="brain-1" page={page} />} />
    <Route path="/brains/:brainId/sources/:slug" element={<SourceRoute />} />
  </Routes>, `/brains/brain-1/wiki/${page.path}`);
  await userEvent.setup().click(screen.getAllByRole('link', { name: label('Shore Book') })[0]!);
  expect(await screen.findByText('Source view /brains/brain-1/sources/shore#passage=120-245')).toBeInTheDocument();
});
