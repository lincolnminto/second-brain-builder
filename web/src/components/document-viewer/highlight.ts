/**
 * A rehype plugin that wraps ranges of the Markdown source in `<mark>` elements.
 *
 * react-markdown keeps the source position of each text node, so a range of the
 * source string (a cited passage, a find match) maps to the text nodes it covers.
 * The hast types are not a direct dependency, so this file declares the small part
 * of them that it uses.
 */

export type HighlightKind = 'passage' | 'match' | 'current';

/** A range of the Markdown source string (UTF-16 indexes) to highlight. */
export interface HighlightRange {
  start: number;
  end: number;
  kind: HighlightKind;
}

export interface HighlightOptions {
  /** The exact string that react-markdown renders. */
  source: string;
  ranges: readonly HighlightRange[];
}

interface Point { offset?: number }
interface Position { start: Point; end: Point }
interface TextNode { type: 'text'; value: string; position?: Position }
interface ElementNode {
  type: 'element';
  tagName: string;
  properties: Record<string, string | string[] | boolean>;
  children: HastNode[];
  position?: Position;
}
interface OtherNode { type: 'comment' | 'doctype' | 'raw'; position?: Position }
type HastNode = TextNode | ElementNode | OtherNode;
interface RootNode { type: 'root'; children: HastNode[] }

const KIND_ORDER: readonly HighlightKind[] = ['passage', 'match', 'current'];
/** The opening line of a fenced code block, at the start of its `code` element. */
const FENCE_OPENING = /[ \t]*(?:`{3,}|~{3,})[^\n]*(?:\n|$)/y;

export function rehypeHighlightRanges(options: HighlightOptions) {
  return (tree: RootNode) => {
    if (options.ranges.length) highlightChildren(tree, options);
  };
}

function highlightChildren(parent: RootNode | ElementNode, options: HighlightOptions): void {
  const children: HastNode[] = [];
  for (const child of parent.children) {
    if (child.type === 'element') {
      highlightChildren(child, options);
      children.push(child);
    } else if (child.type === 'text') {
      children.push(...highlightText(child, options, parent));
    } else {
      children.push(child);
    }
  }
  parent.children = children;
}

/** The source span of a text node. `code` marks the text of a code block. */
interface Span {
  start: number;
  end: number;
  code: boolean;
}

/**
 * Where a text node's characters are in the source.
 *
 * Most text nodes have a position. The text of a code block does not: only its
 * `code` element has one, and that span starts with the opening fence line
 * (```` ```text ````), which the text does not show.
 */
function textSpan(node: TextNode, source: string, parent: RootNode | ElementNode): Span | null {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  if (start !== undefined && end !== undefined) return { start, end, code: false };
  if (parent.type !== 'element' || parent.tagName !== 'code') return null;
  const codeStart = parent.position?.start.offset;
  const codeEnd = parent.position?.end.offset;
  if (codeStart === undefined || codeEnd === undefined) return null;
  FENCE_OPENING.lastIndex = codeStart;
  const fence = FENCE_OPENING.exec(source);
  return { start: fence ? codeStart + fence[0].length : codeStart, end: codeEnd, code: true };
}

function textOffsets(value: string, source: string, span: Span): number[] | null {
  if (!span.code) return sourceOffsets(value, source, span.start, span.end);
  // Code text ends with a line break that the source can lack (a fence that runs
  // to the end of the page): map the rest, then put the break after it.
  const trailing = value.endsWith('\n') ? 1 : 0;
  const offsets = sourceOffsets(value.slice(0, value.length - trailing), source, span.start, span.end);
  if (!offsets || !trailing) return offsets;
  offsets.push(Math.min(span.end, (offsets[offsets.length - 1] ?? span.end) + 1));
  return offsets;
}

function highlightText(node: TextNode, { source, ranges }: HighlightOptions, parent: RootNode | ElementNode): HastNode[] {
  const span = node.value ? textSpan(node, source, parent) : null;
  if (!span) return [node];
  const overlapping = ranges.filter(range => range.start < span.end && range.end > span.start);
  if (!overlapping.length) return [node];
  const offsets = textOffsets(node.value, source, span);
  if (!offsets) {
    // The text does not appear in its source span (generated text): mark it whole
    // when a passage covers it, and leave it alone for find.
    const kinds = overlapping.filter(range => range.kind === 'passage').map(range => range.kind);
    return kinds.length ? [mark(node.value, kinds)] : [node];
  }
  const local = overlapping.map(range => ({
    start: valueIndex(offsets, range.start),
    end: valueIndex(offsets, range.end),
    kind: range.kind,
  }));
  const cuts = new Set([0, node.value.length]);
  for (const range of local) {
    cuts.add(range.start);
    cuts.add(range.end);
  }
  const points = [...cuts].sort((left, right) => left - right);
  const pieces: HastNode[] = [];
  for (let index = 0; index + 1 < points.length; index += 1) {
    const from = points[index] ?? 0;
    const to = points[index + 1] ?? 0;
    if (to <= from) continue;
    const value = node.value.slice(from, to);
    const kinds = local.filter(range => range.start <= from && range.end >= to).map(range => range.kind);
    pieces.push(kinds.length ? mark(value, kinds) : { type: 'text', value });
  }
  return pieces;
}

/**
 * The source offset of each character of `value`, plus the offset after it.
 *
 * Text can differ from its source span: escapes (`\*`), character references
 * (`&amp;`), the code fence around code, and white space that rendering trims. Each
 * character of the value is matched to the next equal character of the source.
 * Returns null when the value does not appear in order in its span.
 */
const NAMED_ENTITY = /^&[A-Za-z][A-Za-z0-9]*;/;
const DECIMAL_ENTITY = /^&#\d+;/;
const HEX_ENTITY = /^&#x[0-9A-Fa-f]+;/;

/** The character one HTML character reference decodes to, or null when it is not one. */
function decodeEntityReference(reference: string): string | null {
  const decimal = /^&#(\d+);$/.exec(reference);
  if (decimal) return String.fromCodePoint(Number(decimal[1]));
  const hex = /^&#x([0-9A-Fa-f]+);$/i.exec(reference);
  if (hex?.[1]) return String.fromCodePoint(Number.parseInt(hex[1], 16));
  const named = NAMED_ENTITY.exec(reference);
  if (!named || typeof document === 'undefined') return null;
  const span = document.createElement('span');
  span.innerHTML = reference;
  const text = span.textContent;
  return text && text !== reference ? text : null;
}

function entityLength(source: string, cursor: number, end: number): number | null {
  if (cursor >= end || source.charCodeAt(cursor) !== 0x26) return null;
  const tail = source.slice(cursor, end);
  for (const pattern of [HEX_ENTITY, DECIMAL_ENTITY, NAMED_ENTITY]) {
    const match = pattern.exec(tail);
    if (match) return match[0].length;
  }
  return null;
}

/** How many source characters encode `character` at `cursor`, or null when they do not. */
function encodedLength(source: string, cursor: number, end: number, character: string): number | null {
  if (cursor >= end) return null;
  if (source.charCodeAt(cursor) === 0x5c && cursor + 1 < end && source.charAt(cursor + 1) === character) return 2;
  const reference = entityLength(source, cursor, end);
  if (reference !== null && decodeEntityReference(source.slice(cursor, cursor + reference)) === character) return reference;
  return null;
}

/** A character reference or backslash escape at `cursor`, whether or not it matches the value character. */
function skipEncodedRun(source: string, cursor: number, end: number): number | null {
  const reference = entityLength(source, cursor, end);
  if (reference !== null) return reference;
  if (source.charCodeAt(cursor) === 0x5c && cursor + 1 < end) return 2;
  return null;
}

function sourceOffsets(value: string, source: string, start: number, end: number): number[] | null {
  const offsets = new Array<number>(value.length + 1);
  if (source.slice(start, end) === value) {
    for (let index = 0; index <= value.length; index += 1) offsets[index] = start + index;
    return offsets;
  }
  let cursor = start;
  for (let index = 0; index < value.length; index += 1) {
    const character = value.charAt(index);
    let matched = false;
    while (!matched && cursor < end) {
      const amp = source.lastIndexOf('&', cursor);
      if (amp >= start) {
        const length = entityLength(source, amp, end);
        if (length && cursor > amp && cursor < amp + length) {
          cursor = amp + length;
          continue;
        }
      }
      if (source.charAt(cursor) === character) {
        offsets[index] = cursor;
        cursor += 1;
        matched = true;
        break;
      }
      const encoded = encodedLength(source, cursor, end, character);
      if (encoded !== null) {
        offsets[index] = cursor;
        cursor += encoded;
        matched = true;
        break;
      }
      const skip = skipEncodedRun(source, cursor, end);
      if (skip !== null) {
        cursor += skip;
        continue;
      }
      cursor += 1;
    }
    if (!matched) return null;
  }
  offsets[value.length] = cursor;
  return offsets;
}

/** The index of the first character of the value at or after source offset `offset`. */
function valueIndex(offsets: readonly number[], offset: number): number {
  let low = 0;
  let high = offsets.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if ((offsets[middle] ?? 0) < offset) low = middle + 1;
    else high = middle;
  }
  return low;
}

function mark(value: string, kinds: readonly HighlightKind[]): ElementNode {
  const present = KIND_ORDER.filter(kind => kinds.includes(kind));
  const properties: ElementNode['properties'] = {
    className: ['dv-mark', ...present.map(kind => `dv-mark-${kind}`)],
  };
  for (const kind of present) properties[`data-dv-${kind}`] = 'true';
  return { type: 'element', tagName: 'mark', properties, children: [{ type: 'text', value }] };
}
