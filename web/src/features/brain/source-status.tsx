import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useCurrentBrain } from '../../app/context';
import type { Schema } from '../../api/client';
import { api, result } from '../../api/client';
import { StatusBadge } from '../../components/ui/badge';
import { partProgressPercent } from '../source/ingest';
import { t } from './strings';

export function SourceListStatus({ brainId, source }: { brainId: string; source: Schema['SourceView'] }) {
  const { events } = useCurrentBrain();
  const estimate = useQuery({
    queryKey: ['ingest-estimate', brainId, source.slug],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/sources/{slug}/ingest-estimate', {
      params: { path: { brain_id: brainId, slug: source.slug } },
    })),
    enabled: source.status === 'partly_ingested',
  });
  useEffect(() => {
    if (source.status !== 'partly_ingested') return;
    const relevant = events.some(event => event.brain_id === source.brain_id && event.data.source === source.slug);
    if (relevant) void estimate.refetch();
  }, [events, source.brain_id, source.slug, source.status, estimate]);
  if (source.status !== 'partly_ingested') return <StatusBadge status={source.status} />;
  const partsTotal = estimate.data?.parts_total ?? 0;
  const partsDone = estimate.data?.parts_done ?? 0;
  const percent = partProgressPercent(partsDone, partsTotal);
  return <span className="badge status-partly_ingested">{t('partlyIngestedBadge', { percent })}</span>;
}