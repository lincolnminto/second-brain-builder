import { expect, it } from 'vitest';
import { highlightTerms, keyTerms, sentencesAt } from './citations';
import { citedSourceUrl, passageUrl, sourceSlug } from './resolution';

it('takes the sentence that holds each reference, as the citations API reads it', () => {
  // Each offset is where a footnote reference sits in the text without its marks.
  const text = 'The grey heron waits for fish (Shore Book 11:1-9). It wades into pools at low tide. The heron nests in tall trees (see chapter 3) near the coast.';
  const offsets = [text.indexOf(' It wades'), text.indexOf(' The heron'), text.indexOf(' nests'), text.length];
  expect(sentencesAt(text, offsets)).toEqual([
    'The grey heron waits for fish (Shore Book 11:1-9).',
    'It wades into pools at low tide.',
    'The heron nests in tall trees (see chapter 3) near the coast.',
    'The heron nests in tall trees (see chapter 3) near the coast.',
  ]);
  // Periods inside parentheses do not end a sentence; a reference after a space starts the next one.
  const cited = 'A later paragraph (cf. Gen. 2:4) cites twice. Next one.';
  expect(sentencesAt(cited, [cited.indexOf(' Next'), cited.indexOf('Next')])).toEqual(['A later paragraph (cf. Gen. 2:4) cites twice.', 'Next one.']);
});

it('takes the same sentence as search.cited_sentence when whitespace or a closing quote is next to a reference', () => {
  // "|" is a footnote reference. The expected sentences are the output of the server function for the same bodies.
  const at = (marked: string) => {
    const parts = marked.split('|');
    const offsets = parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('').length);
    return sentencesAt(parts.join(''), offsets);
  };
  expect(at('The heron waits for fish. | It wades into pools at low tide.')).toEqual(['It wades into pools at low tide.']);
  expect(at('The heron waits for fish.| | It wades into pools at low tide.')).toEqual(['The heron waits for fish.', 'It wades into pools at low tide.']);
  expect(at('The heron said "wait for fish.|" It wades into pools at low tide.')).toEqual(['The heron said "wait for fish." It wades into pools at low tide.']);
  expect(at('Fish!"| Next one.')).toEqual(['Fish!"']);
  expect(at('Wait.|.. Next one.')).toEqual(['Wait...']);
  expect(at('The heron waits (by the shore.| Really.) for fish. Next.')).toEqual(['The heron waits (by the shore. Really.) for fish.']);
});

it('takes the topic words of the cited sentence and highlights them without case or diacritics', () => {
  expect(keyTerms('The heron nests in tall trees (see chapter 3) near the coast.')).toEqual(['heron', 'nests', 'tall', 'trees', 'chapter', 'near', 'coast']);
  expect(keyTerms('Is it so?')).toEqual(['is', 'it', 'so']);
  const parts = highlightTerms('Gênesis tells of HERONS and a heron.', keyTerms('genesis heron'));
  expect(parts).toEqual([
    { text: 'Gênesis', match: true }, { text: ' tells of HERONS and a ', match: false }, { text: 'heron', match: true }, { text: '.', match: false },
  ]);
  expect(highlightTerms('No match here.', [])).toEqual([{ text: 'No match here.', match: false }]);
});

it('opens the Source view of a Canonical Document at the passage', () => {
  expect(sourceSlug('sources/nature/coastal-birds/coastal-birds.md')).toBe('coastal-birds');
  expect(sourceSlug('wiki/entities/heron.md')).toBeNull();
  expect(sourceSlug('sources/notes/other.md')).toBeNull();
  const passage = { source_id: 'coastal-birds', source_path: 'sources/nature/coastal-birds/coastal-birds.md', source_title: 'Coastal Birds', char_start: 120, char_end: 245, heading_trail: [], text: '', score: 1, method: 'terms' as const };
  expect(passageUrl('my brain', passage)).toBe('/brains/my%20brain/sources/coastal-birds#passage=120-245');
  expect(citedSourceUrl('../../sources/nature/coastal-birds/coastal-birds.md', 'wiki/entities/heron.md', 'my brain')).toBe('/brains/my%20brain/sources/coastal-birds');
  expect(citedSourceUrl('https://example.com/sources/a/a.md', 'wiki/entities/heron.md', 'my brain')).toBeNull();
});
