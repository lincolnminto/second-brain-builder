function normalizeHeadingText(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLowerCase();
}

/** Drop a leading ATX H1 when it repeats the page title shown in the view header. */
export function stripLeadingDuplicateH1(body: string, title: string): string {
  const expected = normalizeHeadingText(title);
  if (!expected) return body;
  const lines = body.split(/\r?\n/);
  let index = 0;
  while (index < lines.length && (lines[index] ?? '').trim() === '') index += 1;
  if (index >= lines.length) return body;
  const first = lines[index] ?? '';
  const match = first.match(/^#\s+(.+?)\s*$/);
  if (!match?.[1] || normalizeHeadingText(match[1]) !== expected) return body;
  let next = index + 1;
  while (next < lines.length && (lines[next] ?? '').trim() === '') next += 1;
  return lines.slice(next).join('\n');
}