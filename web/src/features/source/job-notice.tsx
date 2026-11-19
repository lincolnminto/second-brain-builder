import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import type { Schema } from '../../api/client';
import { t } from './strings';

export function JobNotice({ job, status, root }: { job: Schema['JobView']; status: Schema['JobView']['status']; root: string }) {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    if (status !== 'succeeded') return;
    const timer = setTimeout(() => setHidden(true), 4000);
    return () => clearTimeout(timer);
  }, [status]);
  if (hidden) return null;
  return <div role="status" className="notice source-queued"><div>
    <p>{t(status === 'queued' && job.kind === 'source.delete' ? 'deleteQueued' : status, { id: job.id })}</p>
    <Link to={`${root}?activity=1`}>{t('viewActivity')}</Link>
  </div></div>;
}
