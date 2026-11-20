import { useEffect, useId, useRef, useState } from 'react';
import { ChevronRight, Folder, FolderOpen, FolderPlus, LoaderCircle, PenLine, Trash2, X } from 'lucide-react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router';
import { useCreateFolder, useDeleteFolder, useFolders, useJobs, useMoveSource, useRenameFolder } from '../api/hooks';
import { Button } from '../components/ui/button';
import { ConfirmDialog } from '../components/ui/dialog';
import { ErrorNotice } from '../components/ui/feedback';
import { Input } from '../components/ui/input';
import { cn } from '../components/ui/utils';
import { t } from '../features/brain/strings';
import { t as appText } from '../i18n/en';
import { sourceDrop, useSourceDropTarget, type SourceDrag } from './folder-drag';
import { RowMenu } from './folder-menu';
import {
  MAX_FOLDER_DEPTH, MAX_FOLDER_LENGTH, blockingChange, deleteBlock, folderName, folderPaths, folderRows, folderUrl, nonSlugFolder,
  parentFolder, pendingFolderChanges, pendingPlacements, previewNewFolder, previewRename, within,
  type DeleteBlock, type FolderChange, type FolderPreview, type FolderProblem, type FolderRow,
} from './folder-paths';
import './folder-tree.css';

type Edit = { kind: 'create'; parent: string } | { kind: 'rename'; folder: string };
type Tracked = FolderChange;
type FocusTarget = { path: string } | 'new';
/** A failed request that no open form shows: a drop, or an edit the user left. */
type Notice = { key: number; message: string };

type Tree = {
  root: string; selected: string | null; collapsed: ReadonlySet<string>; edit: Edit | null;
  changes: FolderChange[]; placements: string[]; existing: ReadonlySet<string>;
  saving: boolean; editError: unknown;
  toggle: (path: string) => void; startCreate: (parent: string) => void; startRename: (folder: string) => void;
  askDelete: (row: FolderRow) => void; submit: (path: string) => void; cancel: () => void;
  dropOn: (target: string) => (drag: SourceDrag | null) => void;
  linkRef: (path: string) => (element: HTMLAnchorElement | null) => void; onNavigate: () => void;
};

type TreeProps = { brainId: string; onNavigate: () => void };

// A new Brain starts with a fresh tree: no open input, collapsed rows, or tracked jobs.
export function FolderTree(props: TreeProps) {
  return <BrainFolderTree key={props.brainId} {...props} />;
}

function BrainFolderTree({ brainId, onNavigate }: TreeProps) {
  const folders = useFolders(brainId);
  const jobs = useJobs(brainId);
  const create = useCreateFolder();
  const rename = useRenameFolder();
  const remove = useDeleteFolder();
  const move = useMoveSource();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [edit, setEditState] = useState<Edit | null>(null);
  // The open edit for request callbacks: a request can end after the user opened another edit.
  const editRef = useRef<Edit | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [deleting, setDeleting] = useState<FolderRow | null>(null);
  const [tracked, setTracked] = useState<Tracked[]>([]);
  const [announcement, setAnnouncement] = useState('');
  const [notices, setNotices] = useState<Notice[]>([]);
  const noticeKey = useRef(0);
  const newButtonId = useId();
  const links = useRef(new Map<string, HTMLAnchorElement>());
  const focusNext = useRef<FocusTarget | null>(null);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  const selected = location.pathname === root || location.pathname === `${root}/` ? params.get('folder') ?? '' : null;
  const changes = pendingFolderChanges(jobs.data);
  const rows = folderRows(folders.data, changes);
  const existing = new Set([...folderPaths(folders.data), ...changes.flatMap(change => [change.folder, change.newFolder ?? change.folder])]);

  useEffect(() => {
    const target = focusNext.current;
    if (!target) return;
    focusNext.current = null;
    (target === 'new' ? document.getElementById(newButtonId) : links.current.get(target.path))?.focus();
  });

  // When a rename or delete of the selected Folder ends, the Brain home follows it.
  useEffect(() => {
    const finished = tracked.filter(item => jobs.data?.some(job => job.id === item.jobId && job.status === 'succeeded'));
    if (!finished.length) return;
    setTracked(list => list.filter(item => !finished.includes(item)));
    if (selected === null) return;
    for (const item of finished) {
      if (item.kind === 'create' || !within(selected, item.folder)) continue;
      const next = item.kind === 'rename' && item.newFolder ? item.newFolder + selected.slice(item.folder.length) : parentFolder(item.folder);
      const query = new URLSearchParams(params);
      if (next) query.set('folder', next); else query.delete('folder');
      const search = query.toString();
      void navigate({ pathname: root, search: search ? `?${search}` : '' }, { replace: true });
      break;
    }
  }, [jobs.data, tracked, selected, params, root, navigate]);

  const failed = tracked.flatMap(item => {
    const job = jobs.data?.find(entry => entry.id === item.jobId && (entry.status === 'failed' || entry.status === 'cancelled'));
    return job ? [{ item, job }] : [];
  });
  const track = (item: Tracked, message: string) => { setTracked(list => [...list, item]); setAnnouncement(message); };
  const notify = (message: string) => setNotices(list => [...list, { key: ++noticeKey.current, message }]);
  const setEdit = (next: Edit | null) => { editRef.current = next; setEditState(next); };
  const closeEdit = (focus: FocusTarget) => { focusNext.current = focus; setEdit(null); };
  const editFocus = (current: Edit): FocusTarget => current.kind === 'rename' ? { path: current.folder } : current.parent ? { path: current.parent } : 'new';
  // A request result acts on its form only while that form is still open.
  const finishEdit = (current: Edit) => { if (editRef.current === current) closeEdit(editFocus(current)); };
  const editFailed = (current: Edit, action: string) => (error: unknown) => {
    if (editRef.current !== current) notify(t('jobFailed', { action, error: errorText(error) }));
  };
  // Radix gives focus back only to a Dialog.Trigger; the delete dialog opens from the row menu.
  const closeDelete = () => { if (deleting) focusNext.current = { path: deleting.path }; setDeleting(null); };

  const tree: Tree = {
    root, selected, collapsed, edit, changes, placements: pendingPlacements(jobs.data), existing,
    saving: edit?.kind === 'rename' ? rename.isPending : create.isPending, editError: edit?.kind === 'rename' ? rename.error : create.error,
    toggle: path => setCollapsed(current => {
      const next = new Set(current);
      if (!next.delete(path)) next.add(path);
      return next;
    }),
    startCreate: parent => {
      create.reset();
      setCollapsed(current => { const next = new Set(current); next.delete(parent); return next; });
      setEdit({ kind: 'create', parent });
    },
    startRename: folder => { rename.reset(); setEdit({ kind: 'rename', folder }); },
    askDelete: row => { remove.reset(); setDeleting(row); },
    // The promise of mutateAsync belongs to its request, so a later reset() or request cannot
    // drop its result (the callbacks of mutate() follow only the latest request).
    submit: path => {
      const current = editRef.current;
      if (!current) return;
      if (current.kind === 'create') {
        create.mutateAsync({ brainId, folder: path }).then(job => {
          track({ jobId: job.id, kind: 'create', folder: path }, t('announceCreate', { path }));
          finishEdit(current);
        }, editFailed(current, t('folder.create')));
      } else {
        rename.mutateAsync({ brainId, folder: current.folder, newFolder: path }).then(job => {
          track({ jobId: job.id, kind: 'rename', folder: current.folder, newFolder: path }, t('announceRename', { from: current.folder, to: path }));
          finishEdit(current);
        }, editFailed(current, t('folder.rename')));
      }
    },
    cancel: () => { if (edit) closeEdit(editFocus(edit)); },
    dropOn: target => drag => {
      const drop = sourceDrop(drag, brainId, target);
      if (drop.action === 'ignore') {
        if (drop.reason === 'sameFolder') setAnnouncement(t('announceSameFolder', { title: drop.title ?? '' }));
        if (drop.reason === 'otherBrain') setAnnouncement(t('announceOtherBrain', { title: drop.title ?? '' }));
        return;
      }
      move.mutateAsync({ brainId, slug: drop.slug, folder: drop.folder }).then(
        () => setAnnouncement(t('announceMove', { title: drop.title, folder: drop.folder || t('rootFolder') })),
        error => notify(t('moveFailed', { title: drop.title, error: errorText(error) })),
      );
    },
    linkRef: path => element => { if (element) links.current.set(path, element); else links.current.delete(path); },
    onNavigate,
  };

  return <section className="folder-tree" aria-labelledby={`${newButtonId}-heading`}>
    <div className="folder-tree-header"><h2 id={`${newButtonId}-heading`}>{t('folders')}</h2>
      <Button id={newButtonId} variant="ghost" size="small" className="folder-new" onClick={() => tree.startCreate('')}><FolderPlus size={15} />{t('newFolder')}</Button>
    </div>
    <ul className="folder-list folder-list-root">
      <RootRow tree={tree} count={folders.data?.total_source_count ?? 0} />
      {rows.map(row => <FolderItem key={row.path} row={row} tree={tree} />)}
      {edit?.kind === 'create' && !edit.parent && <li><FolderNameForm tree={tree} edit={edit} /></li>}
    </ul>
    {!rows.length && folders.isSuccess && edit?.kind !== 'create' && <p className="muted folder-hint">{t('noFolders')}</p>}
    <ErrorNotice error={folders.error} />
    {notices.map(notice => <div key={notice.key} role="alert" className="notice notice-error folder-failure">
      <span>{notice.message}</span>
      <button type="button" className="folder-dismiss" aria-label={t('dismiss')} onClick={() => setNotices(list => list.filter(entry => entry !== notice))}><X size={14} /></button>
    </div>)}
    {failed.map(({ item, job }) => <div key={item.jobId} role="alert" className="notice notice-error folder-failure">
      <span>{t('jobFailed', { action: t(`folder.${item.kind}`), error: job.error ?? job.status })}</span>
      <button type="button" className="folder-dismiss" aria-label={t('dismiss')} onClick={() => setTracked(list => list.filter(entry => entry !== item))}><X size={14} /></button>
    </div>)}
    <p className="sr-only" role="status">{announcement}</p>
    <ConfirmDialog open={!!deleting} onOpenChange={open => { if (!open && !remove.isPending) closeDelete(); }}
      title={t('deleteTitle', { name: deleting?.name ?? '' })} description={t('deleteHint')} confirmLabel={t('delete')} pending={remove.isPending}
      onConfirm={() => {
        if (!deleting) return;
        const folder = deleting.path;
        remove.mutate({ brainId, folder }, { onSuccess: job => { track({ jobId: job.id, kind: 'delete', folder }, t('announceDelete', { path: folder })); closeDelete(); } });
      }}><ErrorNotice error={remove.error} /></ConfirmDialog>
  </section>;
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : appText('connectionError');
}

function RootRow({ tree, count }: { tree: Tree; count: number }) {
  const drop = useSourceDropTarget(true, tree.dropOn(''));
  const selected = tree.selected === '';
  return <li className="folder-item">
    <div className={cn('folder-row', selected && 'is-selected', drop.over && 'is-drop-target')} {...drop.props}>
      <span className="folder-toggle" aria-hidden="true" />
      <Link ref={tree.linkRef('')} className="folder-link" to={tree.root} aria-current={selected ? 'page' : undefined} onClick={tree.onNavigate}>
        <FolderOpen size={15} /><span>{t('allSources')}</span>
      </Link>
      {/* All Sources lists every Source, so say where a dropped Source goes. */}
      {drop.over ? <small className="folder-drop-label">{t('dropToRoot')}</small> : <small className="folder-count">{count}</small>}
    </div>
  </li>;
}

function deleteHint(block: DeleteBlock | null) {
  if (!block) return undefined;
  if (block.reason === 'sourcesArriving') return t('deleteArriving');
  return block.count === 1 ? t('deleteHasSource') : t('deleteHasSources', { count: block.count });
}

function pendingLabel(change: FolderChange) {
  if (change.kind === 'rename') return t('pending_rename', { path: change.newFolder ?? '' });
  return t(change.kind === 'create' ? 'pending_create' : 'pending_delete');
}

function FolderItem({ row, tree }: { row: FolderRow; tree: Tree }) {
  const busy = !!blockingChange(row.path, tree.changes);
  // A Folder whose name (or a parent's name) is not a slug takes no Source or new Folder:
  // the server would put it at the slug path, outside this Folder.
  const notSlug = nonSlugFolder(row.path);
  const parentNotSlug = nonSlugFolder(parentFolder(row.path));
  const drop = useSourceDropTarget(!busy && !notSlug, tree.dropOn(row.path));
  const selected = tree.selected === row.path;
  const expanded = !tree.collapsed.has(row.path);
  const renaming = tree.edit?.kind === 'rename' && tree.edit.folder === row.path;
  const creatingInside = tree.edit?.kind === 'create' && tree.edit.parent === row.path;
  const block = deleteBlock(row, tree.placements);
  const busyHint = busy ? t('busy') : undefined;
  return <li className="folder-item">
    {renaming && tree.edit ? <FolderNameForm tree={tree} edit={tree.edit} /> :
      <div className={cn('folder-row', selected && 'is-selected', drop.over && 'is-drop-target', row.ghost && 'is-ghost')} {...drop.props}>
        {row.children.length ? <button type="button" className="folder-toggle" aria-expanded={expanded} aria-label={t(expanded ? 'collapse' : 'expand', { name: row.name })}
          onClick={() => tree.toggle(row.path)}><ChevronRight size={14} /></button> : <span className="folder-toggle" aria-hidden="true" />}
        {row.ghost ? <span className="folder-link"><Folder size={15} /><span>{row.name}</span></span> :
          <Link ref={tree.linkRef(row.path)} className="folder-link" to={folderUrl(tree.root, row.path)} aria-current={selected ? 'page' : undefined} onClick={tree.onNavigate}>
            {selected ? <FolderOpen size={15} /> : <Folder size={15} />}<span>{row.name}</span>
          </Link>}
        {row.pending ? <span className="folder-pending"><LoaderCircle className="spin" size={13} aria-hidden="true" />{pendingLabel(row.pending)}</span> : <small className="folder-count">{row.count}</small>}
        {!row.ghost && <RowMenu label={t('folderActions', { name: row.name })} items={[
          { label: t('newSubfolder'), icon: <FolderPlus size={15} />, onSelect: () => tree.startCreate(row.path), disabled: busy || !!notSlug,
            hint: busyHint ?? (notSlug && t('notSlug', { name: notSlug })) },
          { label: t('rename'), icon: <PenLine size={15} />, onSelect: () => tree.startRename(row.path), disabled: busy || !!parentNotSlug,
            hint: busyHint ?? (parentNotSlug && t('notSlug', { name: parentNotSlug })) },
          { label: t('delete'), icon: <Trash2 size={15} />, onSelect: () => tree.askDelete(row), disabled: busy || !!block, hint: busyHint ?? deleteHint(block) },
        ]} />}
      </div>}
    {(expanded || creatingInside) && (row.children.length > 0 || creatingInside) && <ul className="folder-list">
      {row.children.map(child => <FolderItem key={child.path} row={child} tree={tree} />)}
      {creatingInside && tree.edit && <li><FolderNameForm tree={tree} edit={tree.edit} /></li>}
    </ul>}
  </li>;
}

function problemText(problem: FolderProblem) {
  return t(`problem_${problem}`, { depth: MAX_FOLDER_DEPTH, length: MAX_FOLDER_LENGTH });
}

// The inline input of a new or renamed Folder. It shows the slug path the server will use.
function FolderNameForm({ tree, edit }: { tree: Tree; edit: Edit }) {
  const initial = edit.kind === 'rename' ? folderName(edit.folder) : '';
  const [name, setName] = useState(initial);
  const id = useId();
  const preview: FolderPreview = edit.kind === 'rename' ? previewRename(name, edit.folder, tree.existing) : previewNewFolder(name, edit.parent, tree.existing);
  const changed = name !== initial;
  const label = edit.kind === 'rename' ? t('renameLabel', { name: initial }) : edit.parent ? t('folderNameIn', { parent: edit.parent }) : t('folderName');
  return <form className="folder-form" onSubmit={event => { event.preventDefault(); if (preview.ok && !tree.saving) tree.submit(preview.path); }}>
    <label className="sr-only" htmlFor={id}>{label}</label>
    <Input id={id} autoFocus value={name} placeholder={t('folderPlaceholder')} autoComplete="off" spellCheck={false}
      aria-invalid={!preview.ok && changed ? true : undefined} aria-describedby={`${id}-preview`}
      onFocus={event => event.currentTarget.select()} onChange={event => setName(event.target.value)}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); tree.cancel(); } }} />
    <p id={`${id}-preview`} className={cn('folder-preview', !preview.ok && changed && 'is-problem')}>
      {preview.ok ? t('slugPreview', { path: `sources/${preview.path}/` }) : problemText(preview.problem)}
    </p>
    <div className="folder-form-actions">
      <Button type="submit" size="small" disabled={!preview.ok || tree.saving}>{edit.kind === 'rename' ? t('rename') : t('create')}</Button>
      <Button type="button" variant="ghost" size="small" onClick={tree.cancel}>{t('cancel')}</Button>
    </div>
    <ErrorNotice error={tree.editError} />
  </form>;
}
