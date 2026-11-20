import { stripLeadingDuplicateH1 } from '../markdown-body';
import type { DocumentHeading, DocumentOutline, PassageRange } from './types';

/** How many segments one request loads: the most that the segments route returns. */
export const SEGMENT_PAGE_SIZE = 20;
/** A page with at most this many characters renders in one block, as `SafeMarkdown` does. */
export const SMALL_DOCUMENT_CHARS = 60_000;
/** The most segments of a page that renders in one block: five requests. */
export const SMALL_DOCUMENT_SEGMENTS = 5 * SEGMENT_PAGE_SIZE;

/**
 * Whether a page renders in one block in the page flow, as `SafeMarkdown` does,
 * instead of in the virtualized reader.
 *
 * Every heading starts a segment, so the segment count does not tell a short page
 * from a long one; the size does. One parse also keeps what spans segments whole:
 * footnotes and their definitions, and long tables.
 */
export function isSmallDocument(outline: Pick<DocumentOutline, 'total_chars' | 'segments'>): boolean {
  return outline.segments <= 1 || (outline.total_chars <= SMALL_DOCUMENT_CHARS && outline.segments <= SMALL_DOCUMENT_SEGMENTS);
}

/** Where a segment sits in the body, in code points. */
export interface SegmentSpan {
  index: number;
  char_start: number;
  char_end: number;
}

/**
 * The segment that holds an offset. `exact` is false when the viewer must load
 * segments near `index` to know for sure.
 */
export interface SegmentLocation {
  index: number;
  exact: boolean;
}

/** A range of a segment's `markdown` string, in UTF-16 code units (JavaScript string indexes). */
export interface LocalRange {
  start: number;
  end: number;
}

export function segmentPage(index: number): number {
  return Math.floor(index / SEGMENT_PAGE_SIZE);
}

/**
 * Find the segment that holds body offset `offset`, with the segments loaded so far.
 *
 * Known segment starts bound the answer: segment 0 starts at 0, each heading starts
 * its segment (except a heading in segment 0, which can follow blank lines), and a
 * loaded segment gives its own start and the start of the next one. When the bounds
 * leave more than one segment, the estimate interpolates between them, so loading
 * the page around it either finds the segment or narrows the bounds.
 *
 * Returns null for an empty body (no segments).
 */
export function locateSegment(
  outline: Pick<DocumentOutline, 'total_chars' | 'segments' | 'headings'>,
  loaded: Iterable<SegmentSpan>,
  offset: number,
): SegmentLocation | null {
  const count = outline.segments;
  if (count <= 0) return null;
  const target = Math.min(Math.max(0, Math.trunc(offset)), Math.max(0, outline.total_chars - 1));
  let low = { segment: 0, start: 0 };
  let high = { segment: count, start: outline.total_chars };
  const consider = (segment: number, start: number) => {
    if (segment <= 0 || segment >= count) return;
    if (start <= target) {
      if (segment > low.segment) low = { segment, start };
    } else if (segment < high.segment) {
      high = { segment, start };
    }
  };
  for (const heading of outline.headings) consider(heading.segment, heading.char_start);
  for (const segment of loaded) {
    if (segment.char_start <= target && target < segment.char_end) return { index: segment.index, exact: true };
    consider(segment.index, segment.char_start);
    consider(segment.index + 1, segment.char_end);
  }
  const span = high.segment - low.segment;
  // Segments are never empty, so a known start at the offset is its segment.
  if (span <= 1 || low.start === target) return { index: Math.min(low.segment, count - 1), exact: true };
  const fraction = (target - low.start) / Math.max(1, high.start - low.start);
  const index = Math.min(high.segment - 1, Math.max(low.segment, low.segment + Math.floor(fraction * span)));
  return { index, exact: false };
}

/**
 * The UTF-16 index in `text` after its first `codePoints` code points.
 *
 * The API counts code points (as Python does); JavaScript strings count UTF-16 code
 * units, which differ for characters outside the Basic Multilingual Plane.
 */
export function codePointToUtf16(text: string, codePoints: number): number {
  let units = 0;
  for (let count = 0; count < codePoints && units < text.length; count += 1) {
    const code = text.charCodeAt(units);
    const pair = code >= 0xd800 && code <= 0xdbff && units + 1 < text.length;
    units += pair && isLowSurrogate(text.charCodeAt(units + 1)) ? 2 : 1;
  }
  return units;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/**
 * The part of `range` that falls in one segment, as indexes into its `markdown`.
 *
 * Returns null when the range does not overlap the segment. An empty range at an
 * offset inside the segment gives an empty local range, so the viewer can still
 * scroll to it.
 */
export function localRange(segment: SegmentSpan & { markdown: string }, range: PassageRange): LocalRange | null {
  const start = Math.max(range.char_start, segment.char_start);
  const end = Math.min(range.char_end, segment.char_end);
  const empty = range.char_start === range.char_end && start === end && start < segment.char_end;
  if (end <= start && !empty) return null;
  const local = codePointToUtf16(segment.markdown, start - segment.char_start);
  return { start: local, end: empty ? local : codePointToUtf16(segment.markdown, end - segment.char_start) };
}

/** The index in `headings` of the last heading at or before `segment`, or -1. */
export function headingAt(headings: readonly DocumentHeading[], segment: number): number {
  let low = 0;
  let high = headings.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if ((headings[middle]?.segment ?? 0) <= segment) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

/** The headings that `segment` sits under, outermost first, from the outline. */
export function headingTrail(headings: readonly DocumentHeading[], segment: number): string[] {
  const trail: string[] = [];
  let level = Number.POSITIVE_INFINITY;
  for (let index = headingAt(headings, segment); index >= 0 && level > 1; index -= 1) {
    const heading = headings[index];
    if (!heading || heading.level >= level) continue;
    trail.unshift(heading.text);
    level = heading.level;
  }
  return trail;
}

const LEADING_H1 = /^(?:[ \t]*\r?\n)*#[ \t]+[^\r\n]*(?:\r?\n|$)(?:[ \t]*\r?\n)*/;

/**
 * How many characters to skip at the start of the first segment so that an H1
 * that repeats the page title is not shown twice (as `SafeMarkdown` does).
 *
 * The viewer renders `markdown.slice(length)` and shifts its highlight ranges by
 * the same length.
 */
export function leadingTitleLength(markdown: string, title: string): number {
  if (stripLeadingDuplicateH1(markdown, title) === markdown) return 0;
  return LEADING_H1.exec(markdown)?.[0].length ?? 0;
}
