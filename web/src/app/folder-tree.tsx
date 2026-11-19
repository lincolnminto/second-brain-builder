import { Folder, FolderOpen } from 'lucide-react';
import { Link } from 'react-router';
import { useFolders } from '../api/hooks';
import type { Schema } from '../api/client';
import { t } from '../i18n/en';
import { ErrorNotice } from '../components/ui/feedback';
export function FolderTree({ brainId, onNavigate }: { brainId: string; onNavigate: () => void }) {
  const folders = useFolders(brainId);
  return <section className="folder-tree"><h2>{t('folders')}</h2><Link to={`/brains/${encodeURIComponent(brainId)}`} onClick={onNavigate}><FolderOpen size={16} /><span>{t('allSources')}</span><small>{folders.data?.total_source_count ?? 0}</small></Link>
    {folders.data?.children?.map(folder => <FolderBranch key={folder.path} folder={folder} brainId={brainId} onNavigate={onNavigate} />)}
    {!folders.data?.children?.length && <p className="muted folder-hint">{t('noFolders')}</p>}
    <ErrorNotice error={folders.error} />
  </section>;
}
function FolderBranch({ folder, brainId, onNavigate }: { folder: Schema['FolderNode']; brainId: string; onNavigate: () => void }) {
  return <details className="folder-branch" open><summary><Folder size={15} /><Link to={`/brains/${encodeURIComponent(brainId)}?folder=${encodeURIComponent(folder.path)}`} onClick={onNavigate}>{folder.name}</Link><small>{folder.total_source_count}</small></summary>
    <div>{folder.children?.map(child => <FolderBranch key={child.path} folder={child} brainId={brainId} onNavigate={onNavigate} />)}</div>
  </details>;
}
