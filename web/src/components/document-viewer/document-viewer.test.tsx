import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { VirtuosoMockContext } from 'react-virtuoso';
import { mockApi, renderApp } from '../../test/helpers';
import { DocumentViewer } from './document-viewer';
import { SMALL_DOCUMENT_CHARS } from './ranges';
import { testDocument } from './test-document';
import type { PassageRange } from './types';

afterEach(() => vi.unstubAllGlobals());

const PATH = 'sources/field-guide/field-guide.md';

/** Text that makes a page too long to render in one block. */
const FILLER = 'about the delta '.repeat(Math.ceil(SMALL_DOCUMENT_CHARS / 16));

function largeDocument(change: (parts: string[]) => void = () => undefined) {
  // "ferry" appears only in the first page of segments (0 to 19).
  const parts = Array.from({ length: 45 }, (_, index) => index && index % 5 === 0
    ? `## Chapter ${index / 5 + 1}\n\nThe ${index < 20 ? 'ferry' : 'road'} crosses at dawn in chapter ${index / 5 + 1}.\n\n`
    : `Paragraph ${index} about the river delta and its reeds.\n\n`);
  parts[0] = '# Field Guide\n\nAn opening note about the delta.\n\n';
  parts[3] = 'Paragraph 3 🦆 follows the bank. The heron waits by the old mill.\n\n';
  parts[31] = 'Paragraph 31 🦢 crosses the weir. The otter sleeps under the bridge.\n\n';
  parts[44] = `Paragraph 44 ${FILLER}\n`;
  change(parts);
  return testDocument(PATH, 'Field Guide', parts);
}

/** The body range of `text` in one segment, in code points. */
function rangeOf(document: ReturnType<typeof testDocument>, index: number, text: string): PassageRange {
  const segment = document.segments[index]!;
  const start = segment.char_start + [...segment.markdown.slice(0, segment.markdown.indexOf(text))].length;
  return { char_start: start, char_end: start + [...text].length };
}

function renderViewer(document: ReturnType<typeof testDocument>, passage: PassageRange | null = null) {
  const requests = mockApi(document.handler);
  // Tall enough that the list renders segments 0 to 5 (the first chapter) at the top.
  renderApp(<VirtuosoMockContext.Provider value={{ viewportHeight: 3000, itemHeight: 400 }}>
    <DocumentViewer brainId="brain-1" path={PATH} passage={passage} />
  </VirtuosoMockContext.Provider>);
  const segmentStarts = () => requests
    .filter(request => request.url.includes('/documents/segments'))
    .map(request => Number(new URL(request.url).searchParams.get('from')));
  return { requests, segmentStarts };
}

describe('a large document', () => {
  it('renders only the segments near the view and drops the H1 that repeats the title', async () => {
    const { segmentStarts } = renderViewer(largeDocument());
    expect(await screen.findByText('An opening note about the delta.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Field Guide' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Text of Field Guide' })).toBeInTheDocument();
    expect(screen.queryByText(/Paragraph 44/)).not.toBeInTheDocument();
    expect(segmentStarts()).toEqual([0]);
  });

  it('gives the scroll box an explicit height, not the 100% of a parent without one', async () => {
    renderViewer(largeDocument());
    await screen.findByText('An opening note about the delta.');
    // Virtuoso sets height: 100% on the scroll box itself; a class cannot override it.
    expect(screen.getByRole('region', { name: 'Text of Field Guide' }).style.height).toBe('var(--dv-reader-height)');
  });

  it('repeats the header rows of a table before the segment that the table continues into', async () => {
    const document = largeDocument(parts => {
      parts[1] = '| Bird | Count |\n| --- | --- |\n| heron | 3 |\n';
      parts[2] = '| otter | 5 |\n| kingfisher | 8 |\n';
    });
    renderViewer(document, rangeOf(document, 2, 'kingfisher'));
    const continued = (await screen.findByRole('cell', { name: 'otter' })).closest('table')!;
    expect(within(continued).getByRole('columnheader', { name: 'Bird' })).toBeInTheDocument();
    // The passage offsets move past the header rows that the viewer adds.
    expect(window.document.querySelector('[data-dv-passage]')).toHaveTextContent(/^kingfisher$/);
  });

  it('keeps a footnote definition whose reference is in another segment', async () => {
    renderViewer(largeDocument(parts => {
      parts[1] = 'A note follows.[^1]\n\n';
      parts[2] = '[^1]: The note about the delta.\n\n';
    }));
    expect(await screen.findByText(/The note about the delta\./)).toHaveTextContent('[^1]: The note about the delta.');
  });

  it('highlights a passage given in code points after characters outside the BMP', async () => {
    const document = largeDocument();
    renderViewer(document, rangeOf(document, 3, 'heron waits by the old mill'));
    await waitFor(() => expect(window.document.querySelector('[data-dv-passage]')).toHaveTextContent(/^heron waits by the old mill$/));
  });

  // jsdom has no layout, so the list cannot scroll there: these tests check what
  // the viewer loads, not what it shows after a jump.
  it('loads the segments around a passage far into the document first', async () => {
    const document = largeDocument();
    const { segmentStarts } = renderViewer(document, rangeOf(document, 31, 'otter sleeps'));
    await waitFor(() => expect(segmentStarts()[0]).toBe(20));
  });

  it('finds text in the loaded segments and steps through the matches', async () => {
    const user = userEvent.setup();
    renderViewer(largeDocument());
    await screen.findByText('An opening note about the delta.');
    await user.type(screen.getByRole('searchbox', { name: 'Find in text' }), 'FERRY crosses');
    expect(await screen.findByText('1 of 3')).toBeInTheDocument();
    expect(screen.getByText('Find searches the text loaded so far. Scroll to load more.')).toBeInTheDocument();
    await waitFor(() => expect(window.document.querySelector('mark.dv-mark-current')).toHaveTextContent('ferry crosses'));
    await user.keyboard('{Enter}');
    expect(await screen.findByText('2 of 3')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Previous match' }));
    await user.click(screen.getByRole('button', { name: 'Previous match' }));
    expect(await screen.findByText('3 of 3')).toBeInTheDocument();
    await user.click(screen.getByRole('searchbox', { name: 'Find in text' }));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('searchbox', { name: 'Find in text' })).toHaveValue('');
    expect(window.document.querySelector('mark')).toBeNull();
  });

  it('finds and highlights text inside code blocks, but not in their fence lines', async () => {
    const user = userEvent.setup();
    renderViewer(largeDocument(parts => { parts[2] = '```text\nThe ferry log, frame 2.\n```\n\n'; }));
    await screen.findByText('An opening note about the delta.');
    const find = screen.getByRole('searchbox', { name: 'Find in text' });
    await user.type(find, 'frame');
    expect(await screen.findByText('1 of 1')).toBeInTheDocument();
    await waitFor(() => expect(window.document.querySelector('mark.dv-mark-current')).toHaveTextContent(/^frame$/));
    await user.clear(find);
    await user.type(find, 'text');
    expect(await screen.findByText('No matches in loaded text')).toBeInTheDocument();
  });

  it('closes the table of contents on a press outside it, and the toggle still toggles', async () => {
    const user = userEvent.setup();
    renderViewer(largeDocument());
    await screen.findByText('An opening note about the delta.');
    const toggle = screen.getByRole('button', { name: 'Contents' });
    await user.click(toggle);
    expect(screen.getByRole('navigation', { name: 'Contents' })).toBeInTheDocument();
    // The heading trail takes no focus, so only the press closes the popover.
    await user.click(window.document.querySelector('.dv-trail')!);
    expect(screen.queryByRole('navigation', { name: 'Contents' })).not.toBeInTheDocument();
    await user.click(toggle);
    await user.click(screen.getByText('An opening note about the delta.'));
    expect(screen.queryByRole('navigation', { name: 'Contents' })).not.toBeInTheDocument();
    await user.click(toggle);
    await user.click(toggle);
    expect(screen.queryByRole('navigation', { name: 'Contents' })).not.toBeInTheDocument();
  });

  it('jumps to a heading from the table of contents', async () => {
    const user = userEvent.setup();
    const { segmentStarts } = renderViewer(largeDocument());
    await screen.findByText('An opening note about the delta.');
    await user.click(screen.getByRole('button', { name: 'Contents' }));
    const contents = screen.getByRole('navigation', { name: 'Contents' });
    await user.type(within(contents).getByRole('searchbox', { name: 'Filter headings' }), 'chapter 9');
    await user.click(within(contents).getByRole('button', { name: 'Chapter 9' }));
    expect(screen.queryByRole('navigation', { name: 'Contents' })).not.toBeInTheDocument();
    await waitFor(() => expect(segmentStarts()).toContain(40));
  });
});

describe('a small document', () => {
  const markdown = '# Field Guide\n\nFaithful **Canonical Document** text with `inline code` and an escaped \\* star.\n\n<script>bad()</script>\n\nSee [Rivers](../../wiki/concepts/rivers.md).';
  const small = () => testDocument(PATH, 'Field Guide', [markdown]);

  it('renders as one block, without raw HTML, with Brain links kept in the app', async () => {
    renderViewer(small());
    expect(await screen.findByText('Canonical Document', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Field Guide' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByRole('link', { name: 'Rivers' })).toHaveAttribute('href', '/brains/brain-1/wiki/wiki/concepts/rivers.md');
  });

  it('renders a short page with several headings in one block, with footnotes and tables whole', async () => {
    const { segmentStarts } = renderViewer(testDocument(PATH, 'Short Paper', [
      '# Short Paper\n\nAn abstract with a note.[^1]\n\n',
      '## Methods\n\n| Bird | Count |\n| --- | --- |\n| heron | 3 |\n',
      '| otter | 5 |\n\n',
      '## Results\n\nIt worked.\n\n[^1]: The note text.\n',
    ]));
    expect(await screen.findByRole('heading', { name: 'Methods' })).toBeInTheDocument();
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.getByRole('cell', { name: 'otter' })).toBeInTheDocument();
    expect(screen.getByText('The note text.')).toBeInTheDocument();
    // In-page links keep their ids and labels, so the reference and its back link point at each other.
    expect(screen.getByRole('link', { name: '1' })).toHaveAttribute('id', 'user-content-fnref-1');
    expect(screen.getByRole('link', { name: 'Back to reference 1' })).toHaveAttribute('href', '#user-content-fnref-1');
    expect(segmentStarts()).toEqual([0]);
  });

  it('highlights a passage inside a fenced code block', async () => {
    const markdown = '# Field Guide\n\nFrames:\n\n```text\nThe heron waits by the old mill.\n```\n';
    const start = markdown.indexOf('heron');
    renderViewer(testDocument(PATH, 'Field Guide', [markdown]), { char_start: start, char_end: start + 'heron waits'.length });
    expect(await screen.findByText('heron waits', { selector: 'mark' })).toHaveAttribute('data-dv-passage', 'true');
  });

  it('highlights a passage across inline markup, code, and escapes', async () => {
    const doc = small();
    const start = markdown.indexOf('Canonical');
    const end = markdown.indexOf('* star') + '* star'.length;
    renderViewer(doc, { char_start: start, char_end: end });
    await screen.findByText('Canonical Document', { selector: 'mark' });
    const marked = [...document.querySelectorAll('mark[data-dv-passage]')].map(mark => mark.textContent).join('');
    expect(marked).toBe('Canonical Document text with inline code and an escaped * star');
  });
});
