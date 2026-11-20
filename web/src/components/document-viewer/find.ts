/** The most matches that find reports, so a one-letter query stays fast. */
export const MAX_MATCHES = 5_000;

/** One match of find: `markdown.slice(start, end)` of one segment (UTF-16 indexes). */
export interface FindMatch {
  segment: number;
  start: number;
  end: number;
}

export interface FindResult {
  matches: FindMatch[];
  /** More matches exist than `matches` holds. */
  truncated: boolean;
}

const foldedUnits = new Map<string, string>();
const foldedTexts = new WeakMap<object, string>();
const fenceLineCache = new WeakMap<object, readonly FenceLine[]>();
// eslint-disable-next-line no-control-regex
const ASCII = /^[\x00-\x7f]*$/;
const COMBINING_MARK = /^\p{M}$/u;
/**
 * What a combining mark folds to. Text in decomposed form (NFD, common in PDF text)
 * keeps each accent as its own character; find skips these.
 */
const MARK = '\u0000';
// eslint-disable-next-line no-control-regex
const MARKS = /\u0000/g;
const FENCE_LINE = /^ {0,3}(?:`{3,}|~{3,}).*$/gm;

/** A fence line (```` ```text ````) of a segment: `markdown.slice(start, end)`. */
interface FenceLine {
  start: number;
  end: number;
}

function foldUnit(unit: string): string {
  const code = unit.charCodeAt(0);
  if (code < 0x80) return code >= 0x41 && code <= 0x5a ? String.fromCharCode(code + 32) : unit;
  if (code >= 0xd800 && code <= 0xdfff) return unit;
  let folded = foldedUnits.get(unit);
  if (folded === undefined) {
    if (COMBINING_MARK.test(unit)) {
      folded = MARK;
    } else {
      const lower = unit.toLowerCase();
      const base = (lower.length === 1 ? lower : unit).normalize('NFD').charAt(0).toLowerCase();
      folded = base.length === 1 ? base : unit;
    }
    foldedUnits.set(unit, folded);
  }
  return folded;
}

/**
 * Fold text for find: lower case and without diacritics ("Gênesis" → "genesis").
 *
 * The result has the same length as `text`, so an index in it is an index in `text`.
 * A combining mark (an accent in decomposed text) becomes a placeholder that
 * `findPattern` skips.
 */
export function foldForFind(text: string): string {
  if (ASCII.test(text)) return text.toLowerCase();
  let folded = '';
  for (let index = 0; index < text.length; index += 1) folded += foldUnit(text.charAt(index));
  return folded;
}

/** Fold text to compare it with other folded text: `foldForFind` without the mark placeholders. */
export function foldForFilter(text: string): string {
  return foldForFind(text).replace(MARKS, '');
}

function foldedText(segment: { markdown: string }): string {
  let folded = foldedTexts.get(segment);
  if (folded === undefined) {
    folded = foldForFind(segment.markdown);
    foldedTexts.set(segment, folded);
  }
  return folded;
}

/**
 * A pattern for a find query: case and diacritics do not matter, and any run of
 * white space in the query matches any run of white space (or a line break) in the
 * text. The pattern runs on `foldForFind` text, so it also matches across the
 * combining marks of decomposed text. Returns null for a query with no words.
 */
export function findPattern(query: string): RegExp | null {
  const words = foldForFilter(query).split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const marks = `${MARK}*`;
  const word = (text: string) => [...text].map(character => character.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join(marks) + marks;
  return new RegExp(words.map(word).join('\\s+'), 'g');
}

function fenceLines(segment: { markdown: string }): readonly FenceLine[] {
  let lines = fenceLineCache.get(segment);
  if (lines === undefined) {
    lines = [...segment.markdown.matchAll(FENCE_LINE)].map(match => ({ start: match.index, end: match.index + match[0].length }));
    fenceLineCache.set(segment, lines);
  }
  return lines;
}

/**
 * Find `query` in the Markdown of the given segments, in segment order.
 *
 * Find works on the Markdown source: a match that crosses markup (`**a**b` for
 * "ab") is not found, and a match inside markup (a link target) is found but not
 * shown. Fence lines of code blocks (```` ```text ````) are skipped, because the
 * viewer does not show them. Segment objects must not change: the folded text of
 * each one is cached.
 */
export function findMatches(
  segments: Iterable<{ index: number; markdown: string }>,
  query: string,
  limit = MAX_MATCHES,
): FindResult {
  const pattern = findPattern(query);
  const matches: FindMatch[] = [];
  if (!pattern) return { matches, truncated: false };
  const ordered = [...segments].sort((left, right) => left.index - right.index);
  for (const segment of ordered) {
    pattern.lastIndex = 0;
    const text = foldedText(segment);
    const fences = fenceLines(segment);
    let fence = 0;
    for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
      const start = match.index;
      const end = start + match[0].length;
      while (fence < fences.length && (fences[fence]?.end ?? 0) <= start) fence += 1;
      if (fence < fences.length && (fences[fence]?.start ?? 0) < end) continue;
      if (matches.length >= limit) return { matches, truncated: true };
      matches.push({ segment: segment.index, start, end });
    }
  }
  return { matches, truncated: false };
}

/** The index of the first match in `segment` or after it; the first match when none follows. */
export function firstMatchFrom(matches: readonly FindMatch[], segment: number): number {
  if (!matches.length) return -1;
  const index = matches.findIndex(match => match.segment >= segment);
  return index < 0 ? 0 : index;
}

export function sameMatch(left: FindMatch | null, right: FindMatch | null): boolean {
  return !!left && !!right && left.segment === right.segment && left.start === right.start && left.end === right.end;
}
