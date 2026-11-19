import { useState } from 'react';
import { useCancelJob } from '../../api/hooks';
import type { Schema } from '../../api/client';
import { useCurrentBrain } from '../../app/context';
import { Button } from '../../components/ui/button';
import { ConfirmDialog } from '../../components/ui/dialog';
import { ErrorNotice } from '../../components/ui/feedback';
import { t } from './strings';

export function StopJob({ job }: { job: Schema['JobView'] }) {
  const { events } = useCurrentBrain();
  const cancel = useCancelJob();
  const [open, setOpen] = useState(false);
  const stopping = Boolean(job.stop_requested_at) || cancel.isPending || cancel.isSuccess ||
    events.some(event => event.job_id === job.id && event.kind === 'stop_requested');
  return <>
    <Button variant="secondary" size="small" disabled={stopping} onClick={() => { cancel.reset(); setOpen(true); }}>
      {t(stopping ? 'stopping' : 'stop')}
    </Button>
    <ConfirmDialog open={open} onOpenChange={value => { if (!cancel.isPending) setOpen(value); }}
      title={t(job.kind === 'source.ingest' ? 'stopIngestTitle' : 'stopJobTitle')} description={t('stopHint')}
      confirmLabel={t('stop')} pending={cancel.isPending}
      onConfirm={() => cancel.mutate(job.id, { onSuccess: () => setOpen(false) })}>
      <ErrorNotice error={cancel.error} />
    </ConfirmDialog>
  </>;
}
