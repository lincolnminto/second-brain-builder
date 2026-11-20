import { describe, expect, it } from 'vitest';
import { codePointToUtf16, headingTrail, leadingTitleLength, localRange, locateSegment, segmentPage } from './ranges';

const heading = (level: number, text: string, segment: number, char_start: number) => ({ level, text, segment, char_start });

// 10 segments over 1,000 characters. Headings start segments 4 and 7; the first
// heading follows two blank lines in segment 0.
const outline = {
  total_chars: 1000,
  segments: 10,
  headings: [heading(1, 'Field Guide', 0, 2), heading(2, 'Rivers', 4, 400), heading(2, 'Hills', 7, 760)],
};

describe('locateSegment', () => {
  it('estimates between the known segment starts when nothing is loaded', () => {
    expect(locateSegment(outline, [], 100)).toEqual({ index: 1, exact: false });
    expect(locateSegment(outline, [], 700)).toEqual({ index: 6, exact: false });
  });

  it('is exact at a heading, at the start of the body, and between adjacent anchors', () => {
    expect(locateSegment(outline, [], 400)).toEqual({ index: 4, exact: true });
    expect(locateSegment(outline, [], 0)).toEqual({ index: 0, exact: true });
    const adjacent = { ...outline, headings: [...outline.headings, heading(3, 'Fords', 5, 470)] };
    expect(locateSegment(adjacent, [], 450)).toEqual({ index: 4, exact: true });
  });

  it('uses the loaded segments to find or narrow the segment', () => {
    const loaded = [{ index: 4, char_start: 400, char_end: 520 }];
    expect(locateSegment(outline, loaded, 450)).toEqual({ index: 4, exact: true });
    // Segment 4 ends at 520, so 600 is in segment 5 or 6.
    expect(locateSegment(outline, loaded, 600)).toEqual({ index: 5, exact: false });
    const more = [...loaded, { index: 5, char_start: 520, char_end: 650 }];
    expect(locateSegment(outline, more, 600)).toEqual({ index: 5, exact: true });
    expect(locateSegment(outline, more, 700)).toEqual({ index: 6, exact: true });
  });

  it('clamps offsets past the end and has no segment for an empty body', () => {
    const last = { index: 9, char_start: 930, char_end: 1000 };
    expect(locateSegment(outline, [last], 50_000)).toEqual({ index: 9, exact: true });
    expect(locateSegment({ total_chars: 0, segments: 0, headings: [] }, [], 0)).toBeNull();
  });

  it('maps segment indexes to pages of 20', () => {
    expect([0, 19, 20, 45].map(segmentPage)).toEqual([0, 0, 1, 2]);
  });
});

describe('code point offsets', () => {
  it('converts code points to UTF-16 indexes around characters outside the BMP', () => {
    const text = 'a😀b';
    expect([0, 1, 2, 3, 9].map(points => codePointToUtf16(text, points))).toEqual([0, 1, 3, 4, 4]);
  });

  it('cuts a body range to the part inside one segment', () => {
    const segment = { index: 3, char_start: 100, char_end: 110, markdown: 'ab😀cdefghi' };
    const slice = (start: number, end: number) => {
      const local = localRange(segment, { char_start: start, char_end: end });
      return local && segment.markdown.slice(local.start, local.end);
    };
    expect(slice(103, 106)).toBe('cde');
    expect(slice(90, 103)).toBe('ab😀');
    expect(slice(108, 140)).toBe('hi');
    expect(slice(110, 120)).toBeNull();
    expect(slice(50, 60)).toBeNull();
    expect(localRange(segment, { char_start: 105, char_end: 105 })).toEqual({ start: 6, end: 6 });
  });
});

it('builds the heading trail of a segment from the outline', () => {
  const headings = [heading(1, 'Book', 1, 10), heading(2, 'Chapter 1', 2, 50), heading(3, 'Verse', 3, 90), heading(2, 'Chapter 2', 5, 200)];
  expect(headingTrail(headings, 0)).toEqual([]);
  expect(headingTrail(headings, 4)).toEqual(['Book', 'Chapter 1', 'Verse']);
  expect(headingTrail(headings, 6)).toEqual(['Book', 'Chapter 2']);
});

it('skips a leading H1 that repeats the title, with the blank lines after it', () => {
  const markdown = '\n# Field Guide\n\nIntro text.';
  expect(markdown.slice(leadingTitleLength(markdown, 'Field Guide'))).toBe('Intro text.');
  expect(leadingTitleLength(markdown, 'Another title')).toBe(0);
  expect(leadingTitleLength('Intro first.\n# Field Guide', 'Field Guide')).toBe(0);
});
