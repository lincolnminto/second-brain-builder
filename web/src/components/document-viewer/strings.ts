const strings = {
  documentText: 'Text of {title}',
  contents: 'Contents', filterHeadings: 'Filter headings',
  noHeadings: 'This text has no headings.', noHeadingMatches: 'No headings match.',
  find: 'Find in text', findPrevious: 'Previous match', findNext: 'Next match',
  findCount: '{current} of {total}', findCountMore: '{current} of {total}+',
  findTotal: '{total} matches', findTotalMore: '{total}+ matches',
  findNone: 'No matches in loaded text', findNoneAll: 'No matches',
  findHint: 'Find searches the text loaded so far. Scroll to load more.',
  loadingText: 'Loading text…', loadFailed: 'This text did not load.', retry: 'Try again',
  passageMissing: 'The cited passage is past the end of this text.',
} as const;

export function t(key: keyof typeof strings, values: Record<string, string | number> = {}) {
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), strings[key]);
}
