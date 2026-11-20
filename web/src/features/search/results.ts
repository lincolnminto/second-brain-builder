export const searchTypes = ['source', 'summary', 'entity', 'concept', 'comparison', 'synthesis', 'name-index'] as const;

export function searchResultUrl(brainId: string, path: string) {
  return `/brains/${encodeURIComponent(brainId)}/wiki/${path.replace(/^\//, '').split('/').map(encodeURIComponent).join('/')}`;
}

export function searchUrl(brainId: string, query: string) {
  return `/brains/${encodeURIComponent(brainId)}/search?${new URLSearchParams({ q: query.trim() })}`;
}
