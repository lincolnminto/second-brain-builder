import { describe, expect, it } from 'vitest';
import { findMatches, findPattern, firstMatchFrom, foldForFind } from './find';

const segments = [
  { index: 4, markdown: 'The river *Ébre* bends.\nThe RIVER\nbends again.' },
  { index: 1, markdown: 'Gênesis 11: a tower (of brick) and a river.' },
  { index: 2, markdown: 'No match here.' },
];
const texts = (query: string) => {
  const byIndex = new Map(segments.map(segment => [segment.index, segment.markdown]));
  return findMatches(segments, query).matches.map(match => [match.segment, byIndex.get(match.segment)?.slice(match.start, match.end)]);
};

describe('findMatches', () => {
  it('ignores case and diacritics and returns matches in segment order', () => {
    expect(texts('river')).toEqual([[1, 'river'], [4, 'river'], [4, 'RIVER']]);
    expect(texts('genesis')).toEqual([[1, 'Gênesis']]);
    expect(texts('ebre')).toEqual([[4, 'Ébre']]);
  });

  it('matches any white space between words, including line breaks', () => {
    expect(texts('  river   bends ')).toEqual([[4, 'RIVER\nbends']]);
  });

  it('treats the query as text, not as a pattern', () => {
    expect(texts('(of brick)')).toEqual([[1, '(of brick)']]);
    expect(texts('11:')).toEqual([[1, '11:']]);
    expect(texts('.*')).toEqual([]);
  });

  it('finds nothing for an empty query', () => {
    expect(findMatches(segments, '   ')).toEqual({ matches: [], truncated: false });
    expect(findPattern('')).toBeNull();
  });

  it('stops at the limit and says so', () => {
    const result = findMatches([{ index: 0, markdown: 'a a a a a' }], 'a', 3);
    expect(result.matches).toHaveLength(3);
    expect(result.truncated).toBe(true);
  });

  it('ignores the combining marks of decomposed (NFD) text', () => {
    const text = 'Ge\u0302nesis 11 and Gênesis 12.';
    const found = (query: string) => findMatches([{ index: 0, markdown: text }], query).matches.map(match => text.slice(match.start, match.end));
    expect(found('genesis')).toEqual(['Ge\u0302nesis', 'Gênesis']);
    expect(found('Ge\u0302nesis')).toEqual(['Ge\u0302nesis', 'Gênesis']);
    // A match keeps the marks of its last letter, so its highlight covers them.
    expect(findMatches([{ index: 0, markdown: 'Ge\u0302ne\u0302sis' }], 'gene').matches).toEqual([{ segment: 0, start: 0, end: 6 }]);
  });

  it('skips the fence lines of code blocks, which the viewer does not show', () => {
    const markdown = '```text\nThe text of frame 2.\n```\n\n~~~text\nframe 3\n~~~';
    const found = findMatches([{ index: 0, markdown }], 'text').matches;
    expect(found.map(match => markdown.slice(match.start, match.end))).toEqual(['text']);
    expect(found[0]?.start).toBe(markdown.indexOf('text of'));
    expect(findMatches([{ index: 0, markdown }], 'frame').matches).toHaveLength(2);
  });

  it('keeps offsets of the original text for characters outside the BMP', () => {
    const text = '😀 Café 😀 café';
    expect(foldForFind(text)).toHaveLength(text.length);
    const { matches } = findMatches([{ index: 0, markdown: text }], 'CAFE');
    expect(matches.map(match => text.slice(match.start, match.end))).toEqual(['Café', 'café']);
  });
});

it('starts at the first match in or after the segment at the top of the view', () => {
  const matches = [{ segment: 1, start: 0, end: 1 }, { segment: 4, start: 0, end: 1 }, { segment: 4, start: 5, end: 6 }];
  expect(firstMatchFrom(matches, 0)).toBe(0);
  expect(firstMatchFrom(matches, 2)).toBe(1);
  expect(firstMatchFrom(matches, 9)).toBe(0);
  expect(firstMatchFrom([], 3)).toBe(-1);
});
