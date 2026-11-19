const strings = {
  stop: 'Stop', stopping: 'Stopping...',
  stopIngestTitle: 'Stop this Ingest?', stopJobTitle: 'Stop this job?',
  stopHint: 'Work saved so far is kept.',
} as const;

export function t(key: keyof typeof strings) { return strings[key]; }
