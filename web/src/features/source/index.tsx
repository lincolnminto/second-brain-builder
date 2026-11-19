import { Fragment, useState } from 'react';
import { Download, FolderInput, RotateCcw, Sparkles, Trash2 } from 'lucide-react';
import { Link, useParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { originalUrl, type Schema } from '../../api/client';
import { useDeleteSource, useFolders, useIngestSource, useJobs, useMoveSource, usePage, useReconvertSource, useSource } from '../../api/hooks';
import { SafeMarkdown } from '../../components/markdown';
import { StatusBadge, TypeBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { ConfirmDialog, Dialog } from '../../components/ui/dialog';
import { ErrorNotice, Loading, QueryError } from '../../components/ui/feedback';
import { Field, Input, Select } from '../../components/ui/input';
import { sourceJobs } from './jobs';
import { t } from './strings';
import './source.css';

export function SourcePlaceholder() {
  const { brainId } = useCurrentBrain();
  const { slug = '' } = useParams();
  const detail = useSource(brainId, slug);
  if (detail.isPending) return <Loading />;
  if (detail.isError) return <div className="page"><QueryError error={detail.error} retry={() => void detail.refetch()} /></div>;
  return <SourceView key={`${brainId}:${slug}`} brainId={brainId} detail={detail.data} />;
}

function FolderOptions({ node }: { node: Schema['FolderNode'] }) {
  return <Fragment><option value={node.path}>{node.path || t('rootFolder')}</option>{node.children?.map(child => <FolderOptions key={child.path} node={child} />)}</Fragment>;
}

function SourceView({ brainId, detail }: { brainId: string; detail: Schema['SourceDetail'] }) {
  const { source, citing_pages: pages } = detail;
  const { brain, events } = useCurrentBrain();
  const jobs = useJobs(brainId);
  const folders = useFolders(brainId);
  const move = useMoveSource();
  const reconvert = useReconvertSource();
  const ingest = useIngestSource();
  const remove = useDeleteSource();
  const [dialog, setDialog] = useState<'move' | 'delete' | 'reconvert' | null>(null);
  const [folder, setFolder] = useState(source.folder);
  const [queued, setQueued] = useState<Schema['JobView'] | null>(null);
  const related = sourceJobs(source, jobs.data ?? [], events);
  const pending = move.isPending || reconvert.isPending || ingest.isPending || remove.isPending;
  const active = related.some(job => job.status === 'queued' || job.status === 'running') || queued?.status === 'queued' && !jobs.data?.some(job => job.id === queued.id);
  const locked = pending || active || brain?.status !== 'ready';
  const root = `/brains/${encodeURIComponent(brainId)}`;
  function onQueued(job: Schema['JobView']) { setQueued(job); setDialog(null); }
  const input = { brainId, slug: source.slug };
  return <div className="page source-view">
    <Link className="source-back" to={`${root}${source.folder ? `?folder=${encodeURIComponent(source.folder)}` : ''}`}>{t('back')}</Link>
    <header className="page-heading"><div className="source-heading-meta"><TypeBadge type="source" /><StatusBadge status={source.status} /></div>
      <h1>{source.title || source.slug}</h1><p className="mono muted">{source.folder || t('rootFolder')}{source.media_type ? ` / ${source.media_type}` : ''}</p>
    </header>
    <div className="actions source-actions">
      {source.path && <Button asChild variant="secondary"><a href={originalUrl(brainId, source.slug)} download><Download size={16} />{t('download')}</a></Button>}
      <Button variant="secondary" disabled={locked} onClick={() => { setFolder(source.folder); move.reset(); setDialog('move'); }}><FolderInput size={16} />{t('move')}</Button>
      <Button variant="secondary" disabled={locked || !source.path} onClick={() => { reconvert.reset(); setDialog('reconvert'); }}><RotateCcw size={16} />{t('reconvert')}</Button>
      <Button variant="secondary" disabled={locked || ['queued', 'converting'].includes(source.status)} onClick={() => ingest.mutate(input, { onSuccess: onQueued })}><Sparkles size={16} />{t('ingest')}</Button>
      <Button variant="ghost" disabled={locked} onClick={() => { remove.reset(); setDialog('delete'); }}><Trash2 size={16} />{t('delete')}</Button>
    </div>
    <ErrorNotice error={ingest.error} />
    {queued && <div role="status" className="notice source-queued"><div><p>{t(queued.kind === 'source.delete' ? 'deleteQueued' : 'queued', { id: queued.id })}</p><Link to={`${root}?activity=1`}>{t('viewActivity')}</Link></div></div>}
    <section className="source-document" aria-labelledby="canonical-heading"><h2 id="canonical-heading">{t('canonical')}</h2><CanonicalDocument brainId={brainId} source={source} /></section>
    <section className="source-citing" aria-labelledby="citing-heading"><h2 id="citing-heading">{t('citing')}</h2>
      {!pages.length && <p className="muted">{t('noCiting')}</p>}
      <ul>{pages.map(page => <li key={page.path}><Link to={`${root}/wiki/${page.path.split('/').map(encodeURIComponent).join('/')}`}>{page.title}</Link><TypeBadge type={page.type} /></li>)}</ul>
    </section>
    <section className="source-jobs" aria-labelledby="jobs-heading"><h2 id="jobs-heading">{t('jobs')}</h2>
      {jobs.isPending && <Loading />}{jobs.isError && <QueryError error={jobs.error} retry={() => void jobs.refetch()} />}
      {jobs.isSuccess && !related.length && <p className="muted">{t('noJobs')}</p>}
      <ul>{related.map(job => <li key={job.id} className={job.status === 'running' ? 'shimmer' : ''}>
        <div><p>{t(job.kind)} <span className="muted">{t('jobNumber', { id: job.id })}</span></p><small className="muted"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time> · {t('tokens', { count: (job.input_tokens ?? 0) + (job.output_tokens ?? 0) })}</small>{job.error && <p className="error-text">{job.error}</p>}</div><StatusBadge status={job.status} />
      </li>)}</ul>
    </section>
    <Dialog open={dialog === 'move'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('moveTitle')} description={t('moveHint')}>
      <Field label={t('folder')}><Select value={folder} onChange={event => setFolder(event.target.value)}><option value={folder}>{folder || t('rootFolder')}</option>{folders.data && <FolderOptions node={folders.data} />}</Select></Field>
      <Field label={t('newFolder')} hint={t('newFolderHint')}><Input value={folder} onChange={event => setFolder(event.target.value)} placeholder={t('folderExample')} /></Field>
      <ErrorNotice error={folders.error || move.error} /><Button disabled={pending || folder === source.folder} onClick={() => move.mutate({ ...input, folder: folder.trim() }, { onSuccess: onQueued })}>{t('move')}</Button>
    </Dialog>
    <ConfirmDialog open={dialog === 'reconvert'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('reconvertTitle')} description={t('reconvertHint')} confirmLabel={t('reconvert')} pending={pending} onConfirm={() => reconvert.mutate(input, { onSuccess: onQueued })}><ErrorNotice error={reconvert.error} /></ConfirmDialog>
    <ConfirmDialog open={dialog === 'delete'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('deleteTitle')} description={t('deleteHint')} confirmLabel={t('delete')} pending={pending} onConfirm={() => remove.mutate(input, { onSuccess: onQueued })}><ErrorNotice error={remove.error} /></ConfirmDialog>
  </div>;
}

function CanonicalDocument({ brainId, source }: { brainId: string; source: Schema['SourceView'] }) {
  const waiting = ['queued', 'converting'].includes(source.status);
  const page = usePage(brainId, waiting ? '' : source.canonical_path);
  if (waiting) return <p className="muted">{t('convertingHint')}</p>;
  if (page.isPending) return <Loading />;
  if (page.isError) return <QueryError error={page.error} retry={() => void page.refetch()} />;
  return <SafeMarkdown body={page.data.body} brainId={brainId} pagePath={page.data.path} />;
}
