import { en, t, type MessageKey } from '../../i18n/en';
const statusLabels: Record<string, string> = { partly_ingested: 'Partly ingested' };

export function StatusBadge({ status }: { status: string }) {
  const label = status in en ? t(status as MessageKey) : statusLabels[status] ?? status;
  return <span className={`badge status-${status}`}>{label}</span>;
}
const pageTypes = ['source', 'summary', 'entity', 'concept', 'comparison', 'synthesis', 'name-index'] as const;
export function TypeBadge({ type }: { type: string }) {
  const known = pageTypes.find(value => value === type);
  return <span className={`badge type-${known ?? 'source'}`}>{known ? t(`pageType_${known}`) : type}</span>;
}
