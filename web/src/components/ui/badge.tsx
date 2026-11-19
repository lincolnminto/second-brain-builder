import { en, t, type MessageKey } from '../../i18n/en';
export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{status in en ? t(status as MessageKey) : status}</span>;
}
const pageTypes = ['source', 'summary', 'entity', 'concept', 'comparison', 'synthesis'] as const;
export function TypeBadge({ type }: { type: string }) {
  const known = pageTypes.find(value => value === type);
  return <span className={`badge type-${known ?? 'source'}`}>{known ? t(`pageType_${known}`) : type}</span>;
}
