import { useQuery } from '@tanstack/react-query';
import { api, result } from '../client';

export function usePages(brainId: string, types?: string[]) {
  return useQuery({ queryKey: ['pages', brainId, types], queryFn: () => result(api.GET('/api/brains/{brain_id}/pages', { params: { path: { brain_id: brainId }, query: { type: types } } })), enabled: !!brainId });
}
export function usePage(brainId: string, path: string) {
  return useQuery({ queryKey: ['page', brainId, path], queryFn: () => result(api.GET('/api/brains/{brain_id}/pages/{path}', { params: { path: { brain_id: brainId, path } } })), enabled: !!brainId && !!path });
}
export function useGraph(brainId: string) {
  return useQuery({ queryKey: ['graph', brainId], queryFn: () => result(api.GET('/api/brains/{brain_id}/graph', { params: { path: { brain_id: brainId } } })), enabled: !!brainId });
}
export function useSearch(brainId: string, q: string, types?: string[], limit = 20) {
  return useQuery({ queryKey: ['search', brainId, q, types, limit], queryFn: () => result(api.GET('/api/brains/{brain_id}/search', { params: { path: { brain_id: brainId }, query: { q, type: types, limit } } })), enabled: !!brainId && !!q.trim() });
}
