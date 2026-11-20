import { expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import type { Schema } from '../../api/client';
import { renderApp } from '../../test/helpers';
import { WikiMarkdown } from './wiki-markdown';

const page: Schema['PageView'] = {
  path: 'wiki/entities/heron.md', title: 'Heron', type: 'entity', description: null, tags: ['bird'], generated_by: null, generated_at: null,
  sources: [{ id: 'coastal-birds', resource: '/sources/nature/coastal-birds/coastal-birds.md', title: 'Coastal Birds' }],
  body: 'A tall wading bird.[^coastal-birds] Again.[^coastal-birds]\n\n[Wading](../concepts/wading.md#habitat)\n\n![Bird](/sources/nature/coastal-birds/assets/bird.png)\n\n![Remote](https://example.com/tracker.png)\n\n## Habitat\n\nText.\n\n## Habitat\n\n`[^coastal-birds]`\n\n<script>alert(1)</script>\n\n[^coastal-birds]: [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md)',
};

it('links citations to the Source view and keeps the footnote list working', () => {
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const citations = screen.getAllByRole('link', { name: 'Open the cited passage in Coastal Birds' });
  expect(citations.map(citation => citation.getAttribute('href'))).toEqual(['/brains/brain-1/sources/coastal-birds', '/brains/brain-1/sources/coastal-birds']);
  const backReferences = [...container.querySelectorAll('section[data-footnotes] a[data-footnote-backref]')];
  expect(backReferences.map(link => link.getAttribute('href'))).toEqual(citations.map(citation => `#${citation.id}`));
  expect(screen.getAllByRole('link', { name: /^Back to citation/ })).toHaveLength(2);
});

it('routes internal links and images, adds stable heading fragments, and preserves safe Markdown boundaries', () => {
  const { container } = renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  expect(screen.getByRole('link', { name: 'Wading' })).toHaveAttribute('href', '/brains/brain-1/wiki/wiki/concepts/wading.md#habitat');
  expect(screen.getByRole('img', { name: 'Bird' })).toHaveAttribute('src', '/api/brains/brain-1/files/sources/nature/coastal-birds/assets/bird.png');
  expect(screen.queryByRole('img', { name: 'Remote' })).not.toBeInTheDocument();
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByText('[^coastal-birds]')).toHaveProperty('tagName', 'CODE');
  expect(screen.getAllByRole('heading', { name: 'Habitat' }).map(heading => heading.id)).toEqual(['habitat', 'habitat-1']);
});

it('hides a leading H1 that repeats the page title', () => {
  const titled: Schema['PageView'] = { ...page, title: 'Heron', body: '# Heron\n\nBody text.\n\n## Habitat\n\nMore.' };
  renderApp(<WikiMarkdown brainId="brain-1" page={titled} />);
  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  expect(screen.getByText('Body text.')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: 'Habitat' })).toBeInTheDocument();
});

it('keeps a different first heading when the title does not match', () => {
  const distinct: Schema['PageView'] = { ...page, title: 'Heron', body: '# Wading birds\n\nBody text.' };
  renderApp(<WikiMarkdown brainId="brain-1" page={distinct} />);
  expect(screen.getByRole('heading', { level: 1, name: 'Wading birds' })).toBeInTheDocument();
});

it('scrolls to the requested heading after a linked Wiki Page has rendered', () => {
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: scroll, configurable: true });
  try {
    renderApp(<WikiMarkdown brainId="brain-1" page={page} />, '/brains/brain-1/wiki/wiki/entities/heron.md#habitat');
    expect(scroll).toHaveBeenCalledWith({ block: 'start' });
    expect(scroll.mock.instances[0]).toBe(screen.getAllByRole('heading', { name: 'Habitat' })[0]);
  } finally { Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); }
});
