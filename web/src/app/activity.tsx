import { Activity, Radio, RotateCcw } from 'lucide-react';
import { useJobs, useRetryJob } from '../api/hooks';
import { t } from '../i18n/en';
import { useCurrentBrain } from './context';
import { StatusBadge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { ErrorNotice } from '../components/ui/feedback';

export function ActivityRail() {
  const { brainId, events, connected } = useCurrentBrain();
  const jobs = useJobs(brainId || undefined);
  const retry = useRetryJob();
  return <aside className="activity-rail" aria-label={t('activity')}><header className="rail-heading"><span className="inline"><Activity size={16} />{t('activity')}</span><span className="live-label"><Radio size={13} />{t(connected ? 'live' : 'reconnecting')}</span></header>
    <div className="activity-body"><ErrorNotice error={jobs.error || retry.error} />
      {!jobs.data?.length && !events.length && <div className="rail-empty"><span className="quiet-mark"><Activity size={26} strokeWidth={1} /></span><h3>{t('activityEmpty')}</h3><p className="muted">{t('activityHint')}</p></div>}
      {jobs.data?.map(job => <article key={job.id} className={`activity-job ${job.status === 'running' ? 'shimmer' : ''}`}>
        <div className="setting-row"><strong>{t('job', { id: job.id })}</strong><StatusBadge status={job.status} /></div><p className="mono muted">{job.kind}</p>
        {job.error && <p className="error-text">{job.error}</p>}
        <p className="muted">{t('tokensUsed', { count: (job.input_tokens ?? 0) + (job.output_tokens ?? 0) })}</p>
        {events.filter(event => event.job_id === job.id).slice(0, 3).map(event => <p key={event.id} className="event-line">{String(event.data.title ?? event.data.message ?? event.data.status ?? event.kind)}</p>)}
        {['failed', 'cancelled'].includes(job.status) && <Button variant="ghost" size="small" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}><RotateCcw size={14} />{t('retry')}</Button>}
      </article>)}
    </div>
  </aside>;
}
