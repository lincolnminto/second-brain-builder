const strings = {
  back: 'Back to Sources', rootFolder: 'Brain root', download: 'Download original', move: 'Move Source',
  reconvert: 'Reconvert Source', ingest: 'Ingest Source', delete: 'Delete Source', canonical: 'Canonical Document',
  citing: 'Citing Wiki Pages', noCiting: 'No Wiki Pages cite this Source yet. Ingest it to start making connections.',
  jobs: 'Jobs for this Source', noJobs: 'No recent jobs for this Source.', jobNumber: '#{id}', tokens: '{count} tokens',
  queued: 'Job #{id} is queued. Follow its progress in Activity.',
  running: 'Job #{id} is running. Follow its progress in Activity.',
  succeeded: 'Job #{id} succeeded.', failed: 'Job #{id} failed. See Activity for details.', cancelled: 'Job #{id} was cancelled.',
  deleteQueued: 'Deletion is queued as job #{id}. The Source stays visible until the job finishes.', viewActivity: 'Open Activity',
  folder: 'Folder', moveTitle: 'Move this Source', moveHint: 'Its Canonical Document and Assets move with it. Wiki Page citations are updated.',
  newFolder: 'Folder path', newFolderHint: 'Choose an existing Folder above or enter a new path. Leave empty for the Brain root.', folderExample: 'Work/Research',
  reconvertTitle: 'Reconvert this Source?', reconvertHint: 'Conversion recreates the Canonical Document and Assets from the original Source.',
  deleteTitle: 'Delete this Source?', deleteHint: 'This removes the Source, its Canonical Document, and its citations from the Brain. Wiki Pages with no remaining Sources are removed. Git history keeps the file.',
  convertingHint: 'Conversion is preparing the Canonical Document. It will appear here when ready.',
  partlyIngestedProgress: 'Partly ingested · {done} of {total} Parts',
  continueIngest: 'Continue Ingest', continueTitle: 'Continue Ingest',
  continueHint: '{remaining} Parts remain. {tokens}',
  continueHintOne: '1 Part remains. {tokens}',
  runLimit: 'Run token limit', runLimitHint: 'Ingest stops after this many tokens in one run.',
  providerBilling: 'Cloud Providers bill these tokens.',
  continueConfirm: 'Continue', restartIngest: 'Ingest again from the start',
  restartTitle: 'Ingest again from the start?',
  restartHint: 'This clears partial progress and runs Ingest from Part 1.',
  'brain.create': 'Create Brain', 'brain.sync': 'Sync Brain', 'source.add': 'Add Source', 'source.ingest': 'Ingest Source',
  'source.reconvert': 'Reconvert Source', 'source.move': 'Move Source', 'source.delete': 'Delete Source', 'answer.save': 'Save Wiki Page',
} as const;
export function t(key: keyof typeof strings, values: Record<string, string | number> = {}) {
  const template = key === 'continueHint' && values.remaining === 1 ? strings.continueHintOne : strings[key];
  return Object.entries(values).reduce<string>((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), template);
}
