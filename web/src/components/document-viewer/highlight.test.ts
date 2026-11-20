import { describe, expect, it } from 'vitest';
import { rehypeHighlightRanges, type HighlightRange } from './highlight';

type TextNode = {
  type: 'text';
  value: string;
  position: { start: { offset: number }; end: { offset: number } };
};

function root(source: string, value: string) {
  const text: TextNode = {
    type: 'text',
    value,
    position: { start: { offset: 0 }, end: { offset: source.length } },
  };
  return {
    type: 'root' as const,
    children: [{ type: 'element' as const, tagName: 'p', properties: {}, children: [text] }],
  };
}

function markTexts(tree: ReturnType<typeof root>): string[] {
  const texts: string[] = [];
  const walk = (node: unknown) => {
    if (!node || typeof node !== 'object') return;
    const record = node as { type?: string; tagName?: string; value?: string; children?: unknown[] };
    if (record.type === 'element' && record.tagName === 'mark' && record.children) {
      for (const child of record.children) {
        const text = child as TextNode;
        if (text.type === 'text') texts.push(text.value);
      }
    }
    record.children?.forEach(walk);
  };
  tree.children.forEach(walk);
  return texts;
}

function highlight(source: string, value: string, start: number, end: number, kind: HighlightRange['kind'] = 'match') {
  const tree = root(source, value);
  rehypeHighlightRanges({ source, ranges: [{ start, end, kind }] })(tree);
  return markTexts(tree);
}

describe('rehypeHighlightRanges', () => {
  it('maps find marks through HTML character references in the source', () => {
    const source = 'Wait &mdash; the heron waits.';
    const value = 'Wait \u2014 the heron waits.';
    expect(highlight(source, value, source.indexOf('heron'), source.indexOf('heron') + 5)).toEqual(['heron']);
    const ampSource = 'a=1&amp;page=2';
    const ampValue = 'a=1&page=2';
    const pageStart = ampSource.indexOf(';') + 1;
    expect(highlight(ampSource, ampValue, pageStart, pageStart + 4)).toEqual(['page']);
  });

  it('maps marks through backslash escapes in the source', () => {
    const source = 'an escaped \\* star';
    const value = 'an escaped * star';
    const escaped = source.indexOf('\\*');
    expect(highlight(source, value, escaped, escaped + 2, 'passage')).toEqual(['*']);
  });
});