import { Fragment, useState } from 'react';
import { blockingChange, nonSlugFolder, type FolderChange } from '../../app/folder-paths';
import { useMoveSource } from '../../api/hooks';
import type { Schema } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { ErrorNotice } from '../../components/ui/feedback';
import { Field, Select } from '../../components/ui/input';
import { t } from './strings';

/** Why a Folder cannot take a Source now: a pending Folder job, or a name that is not a slug. */
export type Unavailable = (path: string) => 'busy' | 'notSlug' | undefined;

export function FolderOptions({ folders, depth = 0, unavailable }: { folders: Schema['FolderNode'][]; depth?: number; unavailable?: Unavailable }) {
  return folders.map(folder => {
    const name = `${'— '.repeat(depth)}${folder.name}`;
    const reason = unavailable?.(folder.path);
    return <Fragment key={folder.path}>
      <option value={folder.path} disabled={!!reason}>{reason ? t(reason === 'busy' ? 'optionBusy' : 'optionNotSlug', { name }) : name}</option>
      <FolderOptions folders={folder.children ?? []} depth={depth + 1} unavailable={unavailable} />
    </Fragment>;
  });
}

// The keyboard way to do what a drag onto a sidebar Folder does. It offers the same
// Folders as the sidebar: none that a pending Folder job changes, and none that the
// server would turn into another (slug) path.
export function MoveSourceDialog({ brainId, source, folders, changes, onClose, onQueued }: {
  brainId: string; source: Schema['SourceView']; folders?: Schema['FolderNode']; changes: readonly FolderChange[];
  onClose: () => void; onQueued: (folder: string, job: Schema['JobView']) => void;
}) {
  const move = useMoveSource();
  const [folder, setFolder] = useState(source.folder);
  const title = source.title || source.slug;
  const unavailable: Unavailable = path => {
    if (path === source.folder) return undefined;
    if (blockingChange(path, changes)) return 'busy';
    return nonSlugFolder(path) ? 'notSlug' : undefined;
  };
  return <Dialog open onOpenChange={open => { if (!open && !move.isPending) onClose(); }} title={t('moveTitle', { title })} description={t('moveHint')}>
    <form className="stack" onSubmit={event => {
      event.preventDefault();
      if (folder !== source.folder) move.mutate({ brainId, slug: source.slug, folder }, { onSuccess: job => onQueued(folder, job) });
    }}>
      <Field label={t('folder')}><Select value={folder} onChange={event => setFolder(event.target.value)}>
        <option value="">{t('rootFolder')}</option>
        {folders ? <FolderOptions folders={folders.children ?? []} unavailable={unavailable} /> : source.folder && <option value={source.folder}>{source.folder}</option>}
      </Select></Field>
      <ErrorNotice error={move.error} />
      <div className="actions">
        <Button type="button" variant="secondary" disabled={move.isPending} onClick={onClose}>{t('cancel')}</Button>
        <Button type="submit" disabled={move.isPending || folder === source.folder}>{t('move')}</Button>
      </div>
    </form>
  </Dialog>;
}
