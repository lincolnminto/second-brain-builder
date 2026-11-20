/**
 * Pipe tables that continue from one segment into the next.
 *
 * The segments route cuts a block that is longer than a segment at line starts,
 * and a GFM pipe table is one block, so a long table (a spreadsheet or CSV Source)
 * runs over many segments. The viewer parses each segment alone: rows without the
 * header and delimiter rows above them would render as a paragraph of pipes. So
 * the viewer puts the table's header rows before such a segment.
 */

const ROW = /^ {0,3}\|/;
const DELIMITER = /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

/** What one segment does with tables, from its own text. */
interface TableSummary {
  /** The segment starts with table rows that have no header: a table from before continues. */
  continues: boolean;
  /** The number of cells in the first row, when `continues`. */
  columns: number;
  /**
   * The table that is open at the end of the segment: the header rows of a table
   * that starts in it, `carried` for the table it continues, or null.
   */
  end: string | 'carried' | null;
}

const summaries = new WeakMap<object, TableSummary>();

function lines(markdown: string): string[] {
  // The line break at the end does not start another line.
  const text = markdown.endsWith('\n') ? markdown.slice(0, -1) : markdown;
  return text.split('\n').map(line => (line.endsWith('\r') ? line.slice(0, -1) : line));
}

function isDelimiter(line: string | undefined): boolean {
  return line !== undefined && line.includes('|') && DELIMITER.test(line);
}

/** The number of cells in a table row: pipes split cells, except escaped ones. */
export function cellCount(row: string): number {
  let text = row.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
  let cells = 1;
  for (let index = 0; index < text.length; index += 1) {
    const character = text.charAt(index);
    if (character === '\\') index += 1;
    else if (character === '|') cells += 1;
  }
  return cells;
}

function summarize(markdown: string): TableSummary {
  const rows = lines(markdown);
  const continues = ROW.test(rows[0] ?? '') && !isDelimiter(rows[1]);
  let open: string | 'carried' | null = continues ? 'carried' : null;
  let fence: string | null = null;
  for (let index = continues ? 1 : 0; index < rows.length; index += 1) {
    const line = rows[index] ?? '';
    const marker = FENCE.exec(line)?.[1];
    if (fence !== null) {
      if (marker && marker[0] === fence[0] && marker.length >= fence.length) fence = null;
      continue;
    }
    if (marker) {
      fence = marker;
      open = null;
      continue;
    }
    if (!line.trim()) {
      open = null;
      continue;
    }
    // Any other line that is not blank is one more row of an open table.
    if (open !== null) continue;
    const next = rows[index + 1];
    if (line.includes('|') && isDelimiter(next)) {
      open = `${line}\n${next ?? ''}\n`;
      index += 1;
    }
  }
  return { continues, columns: continues ? cellCount(rows[0] ?? '') : 0, end: open };
}

function summaryOf(segment: { markdown: string }): TableSummary {
  let summary = summaries.get(segment);
  if (!summary) {
    summary = summarize(segment.markdown);
    summaries.set(segment, summary);
  }
  return summary;
}

/** Header rows with empty cells, for a table whose header is in a segment that is not loaded. */
export function emptyHeader(columns: number): string {
  const count = Math.max(1, columns);
  return `|${'   |'.repeat(count)}\n|${' --- |'.repeat(count)}\n`;
}

/**
 * The header rows to put before each loaded segment that starts inside a table.
 *
 * The header comes from the loaded segment where the table starts. When the
 * segment just before is not loaded, the viewer cannot know that header for sure:
 * it uses the last table still open before the gap, or empty header cells. A
 * segment whose previous segment is loaded and ends outside a table gets nothing.
 *
 * Segment objects must not change: the result for each one is cached.
 */
export function tableHeaders(segments: Iterable<{ index: number; markdown: string }>): Map<number, string> {
  const ordered = [...segments].sort((left, right) => left.index - right.index);
  const headers = new Map<number, string>();
  let carry: string | null = null;
  let previous = Number.NEGATIVE_INFINITY;
  for (const segment of ordered) {
    const summary = summaryOf(segment);
    let header: string | null = null;
    if (summary.continues && segment.index > 0) {
      header = segment.index === previous + 1 ? carry : carry ?? emptyHeader(summary.columns);
    }
    if (header) headers.set(segment.index, header);
    carry = summary.end === 'carried' ? header : summary.end;
    previous = segment.index;
  }
  return headers;
}
