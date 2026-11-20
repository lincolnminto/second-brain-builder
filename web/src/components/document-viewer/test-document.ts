import { json } from '../../test/helpers';
import type { DocumentHeading, DocumentOutline, DocumentSegment } from './types';

/** A synthetic page split into the given segments, for tests that mock the documents routes. */
export function testDocument(path: string, title: string, parts: readonly string[]) {
  const segments: DocumentSegment[] = [];
  const headings: DocumentHeading[] = [];
  let offset = 0;
  const trail: { level: number; text: string }[] = [];
  parts.forEach((markdown, index) => {
    const length = [...markdown].length;
    const heading = /^(#{1,6})[ \t]+(.+?)[ \t]*$/m.exec(markdown);
    if (heading?.index === 0 && heading[1] && heading[2]) {
      const level = heading[1].length;
      while (trail.length && (trail[trail.length - 1]?.level ?? 0) >= level) trail.pop();
      trail.push({ level, text: heading[2] });
      headings.push({ level, text: heading[2], segment: index, char_start: offset });
    }
    segments.push({ index, char_start: offset, char_end: offset + length, heading_trail: trail.map(item => item.text), markdown });
    offset += length;
  });
  const outline: DocumentOutline = { path, title, total_chars: offset, segments: segments.length, headings };
  /** A `mockApi` handler for the outline and segments routes of this page. */
  const handler = (request: Request, pathname: string) => {
    const url = new URL(request.url);
    if (url.searchParams.get('path') !== path) return undefined;
    if (pathname.endsWith('/documents/outline')) return json(outline);
    if (pathname.endsWith('/documents/segments')) {
      const from = Number(url.searchParams.get('from') ?? 0);
      const count = Number(url.searchParams.get('count') ?? 20);
      return json({ path, total: segments.length, segments: segments.slice(from, from + count) });
    }
    return undefined;
  };
  return { outline, segments, handler };
}
