import { expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { SafeMarkdown } from './markdown';
import { renderApp } from '../test/helpers';
import { resolveBrainPath } from './markdown-path';

it('rejects raw HTML and remote images and routes Brain links and Assets locally', () => {
  const body = '<script>alert(1)</script>\n\n[Entity](/wiki/entities/heron.md)\n\n![Chart](assets/chart.png)\n\n![Remote](https://example.com/tracker.png)\n\n**Match**';
  const { container } = renderApp(<SafeMarkdown body={body} brainId="brain-1" pagePath="sources/paper/paper.md" />);
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByRole('link', { name: 'Entity' })).toHaveAttribute('href', '/brains/brain-1/wiki/wiki/entities/heron.md');
  expect(screen.getByRole('img', { name: 'Chart' })).toHaveAttribute('src', '/api/brains/brain-1/files/sources/paper/assets/chart.png');
  expect(screen.queryByRole('img', { name: 'Remote' })).not.toBeInTheDocument();
  expect(screen.getByText('Match').tagName).toBe('STRONG');
  expect(resolveBrainPath('https://example.com', 'index.md')).toBeNull();
});
