import { strings } from './strings';

export function GraphSkeleton({ compact = false }: { compact?: boolean }) {
  return <div className={`graph-skeleton${compact ? ' graph-skeleton-compact' : ''}`} role="status" aria-label={strings.loading} />;
}