import type { PassageRange } from './types';

const PASSAGE_KEY = 'passage';
const RANGE_PATTERN = /^(\d{1,15})-(\d{1,15})$/;

/**
 * Read a passage from a URL hash such as `#passage=1200-1450`.
 *
 * The numbers are character offsets in the page body (Unicode code points), as the
 * documents and citations routes give them. Other hash parameters are allowed
 * (`#passage=10-20&x=1`). Returns null when the hash holds no valid passage.
 */
export function parsePassageHash(hash: string): PassageRange | null {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const match = RANGE_PATTERN.exec(params.get(PASSAGE_KEY)?.trim() ?? '');
  if (!match) return null;
  const start = Number(match[1]);
  const end = Number(match[2]);
  if (end < start) return null;
  return { char_start: start, char_end: end };
}

/** The URL hash that opens a page at a passage: `#passage=start-end`. */
export function passageHash(range: PassageRange): string {
  return `#${PASSAGE_KEY}=${Math.max(0, Math.trunc(range.char_start))}-${Math.max(0, Math.trunc(range.char_end))}`;
}
