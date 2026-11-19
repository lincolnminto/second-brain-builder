import { expect, it } from 'vitest';
import { groupWikiPages, resolveCitation, resolveWikiImage, resolveWikiLink } from './resolution';

const path = 'wiki/entities/grey-heron.md';
const brainId = 'my brain';
it('routes relative and Brain-root Markdown links, preserving fragments and encoded paths', () => {
  expect(resolveWikiLink('../concepts/wading.md#habitat', path, brainId)).toEqual({ kind: 'internal', to: '/brains/my%20brain/wiki/wiki/concepts/wading.md#habitat' });
  expect(resolveWikiLink('/sources/Field%20study/book/book.md', path, brainId)).toEqual({ kind: 'internal', to: '/brains/my%20brain/wiki/sources/Field%20study/book/book.md' });
  expect(resolveWikiLink('#habitat', path, brainId)).toEqual({ kind: 'anchor', to: '#habitat' });
  expect(resolveWikiLink('https://example.com/read.md', path, brainId)).toEqual({ kind: 'external', to: 'https://example.com/read.md' });
  expect(resolveWikiLink('/sources/book/assets/map.png', path, brainId)).toEqual({ kind: 'asset', to: '/api/brains/my%20brain/files/sources/book/assets/map.png' });
});

it('rejects executable, malformed, and empty links and loads only Brain-local images', () => {
  for (const href of ['javascript:alert(1)', 'data:text/html,hello', 'file:///tmp/test', '', '/wiki/%ZZ.md']) expect(resolveWikiLink(href, path, brainId)).toEqual({ kind: 'blocked' });
  for (const src of ['https://example.com/tracker.png', '//example.com/tracker.png', 'data:image/png;base64,AAAA', '']) expect(resolveWikiImage(src, path, brainId)).toBeNull();
  expect(resolveWikiImage('./assets/sea%20bird.png', 'sources/birds/birds.md', brainId)).toBe('/api/brains/my%20brain/files/sources/birds/assets/sea%20bird.png');
});

it('resolves footnote labels by Source identifier, including normalized and encoded labels', () => {
  const sources = [{ id: 'Field Study', resource: '/sources/study/study.md', title: 'Field Study' }];
  expect(resolveCitation('#user-content-fn-field%20study', sources)).toBe(sources[0]);
  expect(resolveCitation('#user-content-fn-unknown', sources)).toBeUndefined();
  expect(resolveCitation('#user-content-fn-%ZZ', sources)).toBeUndefined();
  expect(resolveCitation('#habitat', sources)).toBeUndefined();
});

it('groups Wiki Pages by reading order and title, excluding Canonical Documents', () => {
  const pages = groupWikiPages([
    { path: 'b.md', type: 'entity', title: 'Bird', description: null },
    { path: 's.md', type: 'summary', title: 'Study', description: null },
    { path: 'a.md', type: 'entity', title: 'Ahab', description: null },
    { path: 'source.md', type: 'source', title: 'Original', description: null },
  ]);
  expect(pages.map(group => [group.type, group.pages.map(page => page.title)])).toEqual([['summary', ['Study']], ['entity', ['Ahab', 'Bird']]]);
});
