const strings = {
  title: 'Wiki', intro: 'Ideas, people, and connections, with a path back to every Source.',
  'name-index': 'Name indexes',
  emptyTitle: 'Give your ideas somewhere to meet', emptyHint: 'Add a Source, then run Ingest. Your Wiki Pages will grow from what you bring.',
  addSource: 'Add a Source', back: 'Back to Wiki', sources: 'Sources', canonical: 'Canonical Document',
  citation: 'Read Canonical Document: {title}', footnotes: 'Citations', backReference: 'Back to citation',
} as const;
export function t(key: keyof typeof strings, values: Record<string, string | number> = {}) {
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), strings[key]);
}
