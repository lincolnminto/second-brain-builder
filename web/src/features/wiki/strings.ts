const strings = {
  title: 'Wiki', intro: 'Ideas, people, and connections, with a path back to every Source.',
  'name-index': 'Name indexes',
  emptyTitle: 'Give your ideas somewhere to meet', emptyHint: 'Add a Source, then run Ingest. Your Wiki Pages will grow from what you bring.',
  addSource: 'Add a Source', back: 'Back to Wiki', sources: 'Sources', canonical: 'Canonical Document',
  footnotes: 'Citations', backReference: 'Back to citation',
  citation: 'Open the cited passage in {title}', openInDocument: 'Open in document', trailSeparator: ' › ',
  passageLoading: 'Finding the cited passage…', passageNotFound: 'No passage of this Canonical Document matches the citation.',
  passageError: 'The cited passage could not be loaded.', retry: 'Try again',
  filters: 'Filter Wiki Pages', typeFilter: 'Filter by page type', allTypes: 'All types',
  folderFilter: 'Folder', allFolders: 'All Folders', clearFolder: 'Clear the Folder filter',
  folderEmptyTitle: 'No Wiki Pages for this Folder yet', folderEmptyHint: 'Wiki Pages show here when they cite a Source in this Folder.',
  emptyType_summary: 'Ingest writes one summary page for each Source',
  emptyType_entity: 'Ingest writes entity pages for the people, places, and works in your Sources',
  emptyType_concept: 'Ingest writes concept pages for the ideas and terms in your Sources',
  emptyType_comparison: 'Ingest writes comparison pages when a Source compares things',
  emptyType_synthesis: 'Synthesis pages come from answers saved by you or your agents',
  'emptyType_name-index': 'Name Index pages list minor names of large Sources',
} as const;
export type WikiStringKey = keyof typeof strings;
export function t(key: WikiStringKey, values: Record<string, string | number> = {}) {
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), strings[key]);
}
