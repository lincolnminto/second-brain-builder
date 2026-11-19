const strings = {
  title: 'Your Brain', intro: 'A place for your Sources, and the connections between them.',
  folder: 'Folder', folderHint: 'Choose a Folder here or in the sidebar. Its nested Folders are included.',
  allSources: 'All Sources', addingTo: 'Add Sources to', rootFolder: 'Brain root',
  sources: 'Sources', emptyTitle: 'Your first Source starts here', emptyFolder: 'This Folder is ready for a Source',
  emptyHint: 'Drop a file above. Conversion creates its Canonical Document; Ingest connects it to your Wiki Pages.',
  recent: 'Recent activity', live: 'Live', reconnecting: 'Reconnecting',
  activityEmpty: 'As you add Sources, their progress will appear here.', jobNumber: '#{id}', tokens: '{count} tokens',
  partlyIngestedBadge: 'Partly ingested {percent}%',
  'brain.create': 'Create Brain', 'brain.sync': 'Sync Brain', 'source.add': 'Add Source',
  'source.ingest': 'Ingest Source', 'source.reconvert': 'Reconvert Source', 'source.move': 'Move Source',
  'source.delete': 'Delete Source', 'answer.save': 'Save Wiki Page',
} as const;
export function t(key: keyof typeof strings, values: Record<string, string | number> = {}) {
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), strings[key]);
}
