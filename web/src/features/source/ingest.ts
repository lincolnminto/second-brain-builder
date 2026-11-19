import { useMutation } from '@tanstack/react-query';
import { api, result, type Schema } from '../../api/client';
import { useInvalidate } from '../../api/hooks/shared';

export const DEFAULT_INGEST_MAX_TOKENS = 400_000;

export function mapReduceProgress(source: Schema['SourceView']): string | null {
  if (source.mode !== 'map_reduce' || !['ingesting', 'partly_ingested'].includes(source.status)) return null;
  if (source.phase === 'map') return `Extracting Parts ${source.map_parts_done ?? 0} of ${source.parts_total ?? 0}`;
  if (source.phase === 'reduce') return `Writing pages ${source.reduce_groups_done ?? 0} of ${source.reduce_groups_total ?? 0}`;
  return null;
}

export function needsIngestDialog(partsTotal: number | null | undefined, estimatedTokens: number): boolean {
  return (partsTotal ?? 0) > 10 || estimatedTokens > DEFAULT_INGEST_MAX_TOKENS;
}

type SourceId = { brainId: string; slug: string };

export function partProgressPercent(partsDone: number, partsTotal: number): number {
  if (partsTotal < 1) return 0;
  return Math.min(100, Math.max(0, Math.round((partsDone / partsTotal) * 100)));
}

export function partProgressLabel(partsDone: number, partsTotal: number): string {
  return `Partly ingested · ${partsDone} of ${partsTotal} Parts`;
}

export function formatTokenEstimate(tokens: number): string {
  if (tokens >= 1_000_000) {
    const value = tokens / 1_000_000;
    const digits = value >= 10 ? 0 : 1;
    return `About ${value.toFixed(digits)} M tokens`;
  }
  if (tokens >= 1_000) {
    const value = tokens / 1_000;
    const digits = value >= 10 ? 0 : 1;
    return `About ${value.toFixed(digits)} K tokens`;
  }
  return `About ${tokens} tokens`;
}

export function remainingParts(partsDone: number, partsTotal: number): number {
  return Math.max(0, partsTotal - partsDone);
}

export function useIngestSourceAction() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ brainId, slug, resume, max_tokens }: SourceId & { resume: boolean; max_tokens?: number | null }) =>
      result(api.POST('/api/brains/{brain_id}/sources/{slug}/ingest', {
        params: { path: { brain_id: brainId, slug } },
        body: { resume, max_tokens, mode: 'auto' },
      })),
    onSuccess: () => invalidate(['jobs'], ['sources'], ['source']),
  });
}
