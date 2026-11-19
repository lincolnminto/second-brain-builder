import type { Schema } from '../../api/client';
import { StatusBadge } from '../../components/ui/badge';
import { partProgressPercent } from '../source/ingest';
import { t } from './strings';

export function SourceListStatus({ source }: { source: Schema['SourceView'] }) {
  if (source.status !== 'partly_ingested' || source.parts_done == null || source.parts_total == null) return <StatusBadge status={source.status} />;
  const percent = partProgressPercent(source.parts_done, source.parts_total);
  return <span className="badge status-partly_ingested">{t('partlyIngestedBadge', { percent })}</span>;
}
