const strings = {
  title: 'Search', intro: 'Find a thought, then follow it back to its Source.', query: 'Search this Brain',
  placeholder: 'A name, an idea, a few words…', filter: 'Page type', all: 'All page types',
  source: 'Canonical Documents', summary: 'Summaries', entity: 'Entities', concept: 'Concepts', comparison: 'Comparisons', synthesis: 'Syntheses',
  'name-index': 'Name indexes',
  emptyTitle: 'What are you thinking about?', emptyHint: 'Search across Canonical Documents and Wiki Pages in this Brain.',
  shortcut: 'You can also open quick search with Ctrl/Cmd+K.', results: 'Search results',
  count: '{count} results for “{query}”', limitedCount: 'First {count} results for “{query}”. Narrow your search to see more.',
  noResults: 'No matching pages yet', noResultsHint: 'Try fewer words or choose another page type.',
  allResults: 'See all results for “{query}”',
} as const;
export function t(key: keyof typeof strings, values: Record<string, string | number> = {}) {
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), strings[key]);
}
