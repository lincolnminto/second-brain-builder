import type { Schema } from '../../api/client';

// source.add receives its slug during Conversion, after it has been queued.
// Its progress/page event is the reliable association when the payload has no slug.
export function sourceJobs(source: Schema['SourceView'], jobs: Schema['JobView'][], events: Schema['JobEventView'][]) {
  const eventJobs = new Set(events.filter(event => event.brain_id === source.brain_id &&
    (event.data.source === source.slug || event.data.path === source.canonical_path)).map(event => event.job_id));
  return jobs.filter(job => job.brain_id === source.brain_id && job.kind.startsWith('source.') &&
    (job.payload.source_slug === source.slug || eventJobs.has(job.id) ||
      job.kind === 'source.add' && !!source.sha256 && job.payload.sha256 === source.sha256))
    .sort((a, b) => b.id - a.id);
}

export function liveJobStatus(job: Schema['JobView'], events: Schema['JobEventView'][]): Schema['JobView']['status'] {
  const latest = events.filter(event => event.job_id === job.id && event.brain_id === job.brain_id && event.kind === 'status')
    .sort((a, b) => b.id - a.id)[0]?.data.status;
  if (['succeeded', 'failed', 'cancelled'].includes(job.status)) return job.status;
  return latest === 'queued' || latest === 'running' || latest === 'succeeded' || latest === 'failed' || latest === 'cancelled' ? latest : job.status;
}
