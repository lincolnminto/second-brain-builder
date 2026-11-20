import { useEffect, useId, useRef, useState, type DragEvent } from 'react';
import { Activity, FileText, FolderInput, LoaderCircle, Radio } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { hasSourceDrag, writeSourceDrag } from '../../app/folder-drag';
import { blockingChange, folderPaths, nonSlugFolder, pendingFolderChanges, pendingMove, type FolderChange } from '../../app/folder-paths';
import { useFolders, useJobs, useSources } from '../../api/hooks';
import type { Schema } from '../../api/client';
import { DropZone } from '../../components/drop-zone';
import { StatusBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { SourceListStatus } from './source-status';
import { FolderOptions, MoveSourceDialog } from './move-source';
import { Field, Select } from '../../components/ui/input';
import { Loading, QueryError } from '../../components/ui/feedback';
import { t } from './strings';
import './brain.css';

export function BrainPlaceholder() {
  const { brainId, brain, events, connected } = useCurrentBrain();
  const [params, setParams] = useSearchParams();
  const folder = params.get('folder') ?? '';
  const folders = useFolders(brainId);
  const sources = useSources(brainId, folder || undefined);
  const jobs = useJobs(brainId);
  const [moving, setMoving] = useState<Schema['SourceView'] | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const recent = [...(jobs.data ?? [])].sort((a, b) => b.id - a.id).slice(0, 5);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  const known = folderPaths(folders.data);
  const targetNew = !!folder && folders.isSuccess && !known.has(folder);
  const changes = pendingFolderChanges(jobs.data);
  // The server refuses uploads into a Folder that a pending job renames or deletes, and puts
  // an upload into a Folder whose name is not a slug at its slug path.
  const notSlug = nonSlugFolder(folder);
  const busy = folder ? blockingChange(folder, changes) : undefined;
  const ids = useId();
  const sourceLinkId = (slug: string) => `${ids}-source-${slug}`;
  const moveButtonId = (slug: string) => `${ids}-move-${slug}`;
  // Radix gives focus back only to a Dialog.Trigger, so the Move dialog does it here.
  const focusNext = useRef<string | null>(null);
  useEffect(() => {
    const id = focusNext.current;
    if (!id) return;
    focusNext.current = null;
    document.getElementById(id)?.focus();
  });
  return <div className="page brain-home">
    <header className="page-heading"><h1>{brain?.name ?? t('title')}</h1><p className="muted">{t('intro')}</p></header>
    <div className="brain-folder-bar">
      <Field label={t('folder')} hint={t('folderHint')}><Select value={folder} onChange={event => {
        const next = new URLSearchParams(params);
        if (event.target.value) next.set('folder', event.target.value); else next.delete('folder');
        setParams(next);
      }}><option value="">{t('allSources')}</option>
        {folder && !known.has(folder) && <option value={folder}>{folder}</option>}
        <FolderOptions folders={folders.data?.children ?? []} />
      </Select></Field>
    </div>
    {folders.isError && <QueryError error={folders.error} retry={() => void folders.refetch()} />}
    <div className="brain-drop">
      <p className="brain-target" aria-live="polite"><FolderInput size={16} aria-hidden="true" /><span>{t('addingTo')}</span>{' '}
        <strong className="brain-target-path">{folder ? folder.split('/').join(' / ') : t('rootFolder')}</strong></p>
      {notSlug ? <p className="muted brain-target-note">{t('targetNotSlug', { name: notSlug })}</p>
        : busy ? <p className="muted brain-target-note">{busyText(busy)}</p>
          : targetNew && <p className="muted brain-target-note">{t('targetNew')}</p>}
      {/* Source rows are draggable too, but only the sidebar Folders take them. */}
      <div onDragEnterCapture={ignoreSourceDrag} onDragOverCapture={ignoreSourceDrag} onDropCapture={ignoreSourceDrag}>
        <DropZone key={`${brainId}:${folder}`} brainId={brainId} folder={folder} disabled={brain?.status !== 'ready' || !!notSlug || !!busy} />
      </div>
    </div>
    <section className="brain-sources" aria-labelledby="sources-heading">
      <header className="brain-section-heading"><h2 id="sources-heading">{t('sources')}</h2><span className="muted">{sources.data?.length ?? 0}</span></header>
      {!!sources.data?.length && <p className="muted brain-drag-hint">{t('dragHint')}</p>}
      {sources.isPending && <Loading />}
      {sources.isError && <QueryError error={sources.error} retry={() => void sources.refetch()} />}
      {sources.data?.length === 0 && <div className="brain-empty"><FileText size={24} strokeWidth={1.2} /><h3>{t(folder ? 'emptyFolder' : 'emptyTitle')}</h3><p className="muted">{t('emptyHint')}</p></div>}
      <ul className="brain-source-list">{sources.data?.map(source => {
        const title = source.title || source.slug;
        const target = pendingMove(source.slug, jobs.data);
        return <li key={source.slug} draggable={target === undefined} className={dragging === source.slug ? 'is-dragging' : undefined}
          onDragStart={event => { writeSourceDrag(event.dataTransfer, { brainId, slug: source.slug, folder: source.folder, title }); setDragging(source.slug); }}
          onDragEnd={() => setDragging(null)}>
          <FileText size={20} strokeWidth={1.3} className="muted" />
          <div className="brain-source-title"><Link id={sourceLinkId(source.slug)} to={`${root}/sources/${encodeURIComponent(source.slug)}`}>{title}</Link>
            <p className="mono muted">{source.folder || t('rootFolder')}{source.media_type ? ` / ${source.media_type}` : ''}</p>
            {target !== undefined && <p className="brain-moving"><LoaderCircle className="spin" size={13} aria-hidden="true" />{t('moving', { folder: target || t('rootFolder') })}</p>}</div>
          <SourceListStatus source={source} />
          <Button id={moveButtonId(source.slug)} variant="ghost" size="icon" aria-label={t('moveSource', { title })} title={t('moveSource', { title })} disabled={target !== undefined} onClick={() => setMoving(source)}><FolderInput size={17} /></Button>
        </li>;
      })}</ul>
    </section>
    {/* After a queued move the Move button is locked, so focus goes to the Source link. */}
    {moving && <MoveSourceDialog key={moving.slug} brainId={brainId} source={moving} folders={folders.data} changes={changes}
      onClose={() => { focusNext.current = moveButtonId(moving.slug); setMoving(null); }}
      onQueued={() => { focusNext.current = sourceLinkId(moving.slug); setMoving(null); }} />}
    <section aria-labelledby="recent-heading" className="brain-recent">
      <header className="brain-section-heading"><h2 id="recent-heading" className="inline"><Activity size={18} />{t('recent')}</h2><span className="inline muted"><Radio size={14} />{t(connected ? 'live' : 'reconnecting')}</span></header>
      {jobs.isPending && <Loading />}
      {jobs.isError && <QueryError error={jobs.error} retry={() => void jobs.refetch()} />}
      {jobs.data?.length === 0 && <p className="muted brain-activity-empty">{t('activityEmpty')}</p>}
      <ul className="brain-activity-list">{recent.map(job => {
        const event = events.find(item => item.job_id === job.id);
        const detail = event?.data.title ?? event?.data.message;
        return <li key={job.id} className={job.status === 'running' ? 'shimmer' : ''}><div>
          <p>{t(job.kind)} <span className="muted">{t('jobNumber', { id: job.id })}</span></p>
          {typeof detail === 'string' && <p className="muted">{detail}</p>}
          {job.error && <p className="error-text">{job.error}</p>}
          <small className="muted"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time> · {t('tokens', { count: (job.input_tokens ?? 0) + (job.output_tokens ?? 0) })}</small>
        </div><StatusBadge status={job.status} /></li>;
      })}</ul>
    </section>
  </div>;
}

function busyText(change: FolderChange) {
  if (change.kind === 'rename') return t('targetBusy_rename', { folder: change.folder, path: change.newFolder ?? '' });
  return t(change.kind === 'create' ? 'targetBusy_create' : 'targetBusy_delete', { folder: change.folder });
}

function ignoreSourceDrag(event: DragEvent<HTMLElement>) {
  // Stopped in the capture phase, the drag never reaches the upload zone, which would
  // highlight for it and then ignore the drop. Without preventDefault, no drop is offered.
  if (hasSourceDrag(event.dataTransfer)) event.stopPropagation();
}
