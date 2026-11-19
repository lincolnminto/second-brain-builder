import { useMutation, useQuery } from '@tanstack/react-query';
import { api, result, type Schema } from '../client';
import { useInvalidate } from './shared';

export function useJobs(brainId?: string, status?: Schema['JobStatus'][]) {
  return useQuery({ queryKey: ['jobs', brainId, status], queryFn: () => result(api.GET('/api/jobs', { params: { query: { brain_id: brainId, status } } })), refetchInterval: 10_000 });
}
export function useJob(jobId: number) {
  return useQuery({ queryKey: ['job', jobId], queryFn: () => result(api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } })), enabled: jobId > 0, refetchInterval: (q) => ['queued', 'running'].includes(q.state.data?.status ?? '') ? 3000 : false });
}
export function useRetryJob() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (job_id: number) => result(api.POST('/api/jobs/{job_id}/retry', { params: { path: { job_id } } })), onSuccess: () => invalidate(['jobs'], ['job']) });
}
export function useCancelJob() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (job_id: number) => result(api.POST('/api/jobs/{job_id}/cancel', { params: { path: { job_id } } })), onSuccess: () => invalidate(['jobs'], ['job']) });
}
