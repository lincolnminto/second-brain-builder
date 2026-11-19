import { expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Schema } from '../../api/client';
import { renderApp } from '../../test/helpers';
import { WikiMarkdown } from './wiki-markdown';

const page: Schema['PageView'] = {
  path: 'wiki/entities/heron.md', title: 'Heron', type: 'entity', description: null, tags: ['bird'], generated_by: null, generated_at: null,
  sources: [{ id: 'coastal-birds', resource: '/sources/nature/coastal-birds/coastal-birds.md', title: 'Coastal Birds' }],
  body: 'A tall wading bird.[^coastal-birds] Again.[^coastal-birds]\n\n[Wading](../concepts/wading.md#habitat)\n\n![Bird](/sources/nature/coastal-birds/assets/bird.png)\n\n![Remote](https://example.com/tracker.png)\n\n## Habitat\n\nText.\n\n## Habitat\n\n`[^coastal-birds]`\n\n<script>alert(1)</script>\n\n[^coastal-birds]: [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md)',
};

it('shows an accessible citation card on focus, dismisses with Escape, and links to the Canonical Document', async () => {
  const user = userEvent.setup();
  renderApp(<WikiMarkdown brainId="brain-1" page={page} />);
  const citations = screen.getAllByRole('link', { name: 'Read Canonical Document: Coastal Birds' });
  expect(citations).toHaveLength(2);
  expect(citations[0]).toHaveAttribute('href', '/brains/brain-1/wiki/sources/nature/coastal-birds/coastal-birds.md');
  await user.tab();
  expect(citations[0]).toHaveFocus();
  expect(screen.getByRole('tooltip')).toHaveTextContent('Coastal BirdsCanonical Document');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.hover(citations[1]!);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.unhover(citations[1]!);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
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

it('scrolls to the requested heading after a linked Wiki Page has rendered', () => {
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: scroll, configurable: true });
  try {
    renderApp(<WikiMarkdown brainId="brain-1" page={page} />, '/brains/brain-1/wiki/wiki/entities/heron.md#habitat');
    expect(scroll).toHaveBeenCalledWith({ block: 'start' });
    expect(scroll.mock.instances[0]).toBe(screen.getAllByRole('heading', { name: 'Habitat' })[0]);
  } finally { Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); }
});
