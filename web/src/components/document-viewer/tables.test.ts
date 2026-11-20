import { describe, expect, it } from 'vitest';
import { cellCount, emptyHeader, tableHeaders } from './tables';

const HEADER = '| Name | Value |\n| --- | ---: |\n';

describe('tableHeaders', () => {
  it('repeats the header of a table before each segment that the table continues into', () => {
    const segments = [
      { index: 0, markdown: `# Sheet1\n\n${HEADER}| item 1 | 7 |\n` },
      { index: 1, markdown: '| item 2 | 14 |\n| item 3 | 21 |\n' },
      { index: 2, markdown: '| item 4 | 28 |\n\nAfter the table.\n' },
      { index: 3, markdown: '| a paragraph | that starts with a pipe |\n' },
    ];
    expect(tableHeaders(segments)).toEqual(new Map([[1, HEADER], [2, HEADER]]));
  });

  it('adds nothing for a table that starts at the top of its segment', () => {
    const segments = [
      { index: 0, markdown: 'Intro.\n\n' },
      { index: 1, markdown: `${HEADER}| item 1 | 7 |\n` },
    ];
    expect(tableHeaders(segments).size).toBe(0);
  });

  it('carries the header over segments that are not loaded, or uses empty cells', () => {
    const start = { index: 0, markdown: `${HEADER}| item 1 | 7 |\n` };
    const later = { index: 5, markdown: '| item 90 | 630 |\n' };
    expect(tableHeaders([later, start]).get(5)).toBe(HEADER);
    expect(tableHeaders([later]).get(5)).toBe(emptyHeader(2));
  });

  it('does not take pipes in a fenced code block for a table', () => {
    const segments = [
      { index: 0, markdown: '```text\n| a | b |\n| - | - |\n```\n' },
      { index: 1, markdown: '| not | a row |\n' },
    ];
    expect(tableHeaders(segments).size).toBe(0);
  });
});

it('counts cells without escaped pipes and without the outer pipes', () => {
  expect(cellCount('| a \\| b | c |')).toBe(2);
  expect(cellCount('  | a | b | c')).toBe(3);
});
