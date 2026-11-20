import { Fragment, useState } from 'react';
import { Download, FolderInput, RotateCcw, Sparkles, Trash2 } from 'lucide-react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useCurrentBrain } from '../../app/context';
import { api, originalUrl, result, type Schema } from '../../api/client';
import { useDeleteSource, useFolders, useJobs, useMoveSource, useReconvertSource, useSource } from '../../api/hooks';
import { DocumentViewer, parsePassageHash, useReloadOnPageWrite } from '../../components/document-viewer';
import { StatusBadge, TypeBadge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { ConfirmDialog, Dialog } from '../../components/ui/dialog';
import { ErrorNotice, Loading, QueryError } from '../../components/ui/feedback';
import { Field, Input, Select } from '../../components/ui/input';
import {
  DEFAULT_INGEST_MAX_TOKENS, formatTokenEstimate, mapReduceProgress, needsIngestDialog, partProgressPercent, remainingParts, useIngestSourceAction,
} from './ingest';
import { JobNotice } from './job-notice';
import { StopJob } from '../jobs/stop-job';
import { liveJobStatus, sourceJobs } from './jobs';
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

function PartlyIngestProgress({ partsDone, partsTotal }: { partsDone: number; partsTotal: number }) {
  const percent = partProgressPercent(partsDone, partsTotal);
  const label = t('partlyIngestedProgress', { done: partsDone, total: partsTotal });
  return <div className="source-ingest-progress" aria-label={label}>
    <p className="source-ingest-progress-label">{label}</p>
    <progress className="source-ingest-progress-bar" value={percent} max={100} />
  </div>;
}

function SourceView({ brainId, detail }: { brainId: string; detail: Schema['SourceDetail'] }) {
  const { source, citing_pages: pages } = detail;
  const { brain, events } = useCurrentBrain();
  const jobs = useJobs(brainId);
  const folders = useFolders(brainId);
  const move = useMoveSource();
  const reconvert = useReconvertSource();
  const ingest = useIngestSourceAction();
  const remove = useDeleteSource();
  const [dialog, setDialog] = useState<'move' | 'delete' | 'reconvert' | 'continue' | 'ingest' | 'restart' | null>(null);
  const [folder, setFolder] = useState(source.folder);
  const [maxTokens, setMaxTokens] = useState(String(DEFAULT_INGEST_MAX_TOKENS));
  const [queued, setQueued] = useState<Schema['JobView'] | null>(null);
  const partly = source.status === 'partly_ingested';
  const resume = source.status !== 'ingested';
  const estimate = useQuery({
    queryKey: ['ingest-estimate', brainId, source.slug, resume],
    queryFn: () => result(api.GET('/api/brains/{brain_id}/sources/{slug}/ingest-estimate', {
      params: { path: { brain_id: brainId, slug: source.slug }, query: { resume } },
    })),
    enabled: !['queued', 'converting'].includes(source.status),
  });
  const related = sourceJobs(source, jobs.data ?? [], events);
  const runningIngest = related.find(job => job.kind === 'source.ingest' && liveJobStatus(job, events) === 'running');
  const pending = move.isPending || reconvert.isPending || ingest.isPending || remove.isPending;
  const noticeJob = queued && (jobs.data?.find(job => job.id === queued.id) ?? queued);
  const noticeStatus = noticeJob && liveJobStatus(noticeJob, events);
  const active = related.some(job => ['queued', 'running'].includes(liveJobStatus(job, events))) || noticeStatus === 'queued' || noticeStatus === 'running';
  const locked = pending || active || brain?.status !== 'ready';
  const root = `/brains/${encodeURIComponent(brainId)}`;
  const partsDone = source.parts_done ?? estimate.data?.parts_done ?? 0;
  const partsTotal = source.parts_total ?? estimate.data?.parts_total ?? 0;
  const remaining = remainingParts(estimate.data?.parts_done ?? partsDone, estimate.data?.parts_total ?? partsTotal);
  const tokenEstimate = estimate.data?.estimated_tokens ?? 0;
  function openIngestDialog(kind: 'continue' | 'ingest') {
    ingest.reset();
    setMaxTokens(String(DEFAULT_INGEST_MAX_TOKENS));
    setDialog(kind);
  }
  function onQueued(job: Schema['JobView']) { setQueued(job); setDialog(null); }
  const input = { brainId, slug: source.slug };
  const parsedMaxTokens = Number.parseInt(maxTokens, 10);
  const maxTokensValid = Number.isFinite(parsedMaxTokens) && parsedMaxTokens > 0;
  return <div className="page source-view">
    <Link className="source-back" to={`${root}${source.folder ? `?folder=${encodeURIComponent(source.folder)}` : ''}`}>{t('back')}</Link>
    <div className="content-column">
      <header className="page-heading"><div className="source-heading-meta"><TypeBadge type="source" /><StatusBadge status={source.status} /></div>
        <h1>{source.title || source.slug}</h1><p className="mono muted">{source.folder || t('rootFolder')}{source.media_type ? ` / ${source.media_type}` : ''}</p>
        {(source.notes_dropped_total ?? 0) > 0 && <p>{t('notesDropped', { count: source.notes_dropped_total ?? 0, parts: source.parts_with_drops ?? 0 })}</p>}
        {source.mode === 'map_reduce' && <p>{t('nameIndexCounts', { major: source.reduce_groups_major ?? 0, entries: source.name_index_entries ?? 0, pages: source.name_index_pages ?? 0 })}</p>}
        {mapReduceProgress(source) ? <p role="status">{mapReduceProgress(source)}</p> : partly && partsTotal > 0 && <PartlyIngestProgress partsDone={partsDone} partsTotal={partsTotal} />}
        {partly && estimate.isPending && <Loading />}
        {estimate.isError && <QueryError error={estimate.error} retry={() => void estimate.refetch()} />}
      </header>
      <div className="actions source-actions">
        {runningIngest && <StopJob key={runningIngest.id} job={runningIngest} />}
        {source.path && <Button asChild variant="secondary"><a href={originalUrl(brainId, source.slug)} download><Download size={16} />{t('download')}</a></Button>}
        <Button variant="secondary" disabled={locked} onClick={() => { setFolder(source.folder); move.reset(); setDialog('move'); }}><FolderInput size={16} />{t('move')}</Button>
        <Button variant="secondary" disabled={locked || !source.path} onClick={() => { reconvert.reset(); setDialog('reconvert'); }}><RotateCcw size={16} />{t('reconvert')}</Button>
        {partly ? <>
          <Button variant="secondary" disabled={locked || !estimate.isSuccess} onClick={() => openIngestDialog('continue')}>
            <Sparkles size={16} />{t('continueIngest')}
          </Button>
          <Button variant="ghost" disabled={locked} onClick={() => { ingest.reset(); setDialog('restart'); }}>{t('restartIngest')}</Button>
        </> : <Button variant="secondary" disabled={locked || !estimate.isSuccess || ['queued', 'converting'].includes(source.status)}
          onClick={() => {
            if (needsIngestDialog(source.parts_total ?? estimate.data?.parts_total, tokenEstimate)) openIngestDialog('ingest');
            else ingest.mutate({ ...input, resume: source.status !== 'ingested', max_tokens: null }, { onSuccess: onQueued });
          }}>
          <Sparkles size={16} />{t('ingest')}
        </Button>}
        <Button variant="ghost" disabled={locked} onClick={() => { remove.reset(); setDialog('delete'); }}><Trash2 size={16} />{t('delete')}</Button>
      </div>
      <ErrorNotice error={ingest.error} />
      {noticeJob && noticeStatus && <JobNotice key={noticeJob.id} job={noticeJob} status={noticeStatus} root={root} />}
      <section className="source-document" aria-labelledby="canonical-heading"><h2 id="canonical-heading">{t('canonical')}</h2><CanonicalDocument brainId={brainId} source={source} /></section>
      <section className="source-citing" aria-labelledby="citing-heading"><h2 id="citing-heading">{t('citing')}</h2>
        {!pages.length && <p className="muted">{t('noCiting')}</p>}
        <ul>{pages.map(page => <li key={page.path}><Link to={`${root}/wiki/${page.path.split('/').map(encodeURIComponent).join('/')}`}>{page.title}</Link><TypeBadge type={page.type} /></li>)}</ul>
      </section>
    </div>
    <section className="source-jobs" aria-labelledby="jobs-heading"><h2 id="jobs-heading">{t('jobs')}</h2>
      {jobs.isPending && <Loading />}{jobs.isError && <QueryError error={jobs.error} retry={() => void jobs.refetch()} />}
      {jobs.isSuccess && !related.length && <p className="muted">{t('noJobs')}</p>}
      <ul>{related.map(job => <li key={job.id} className={job.status === 'running' ? 'shimmer' : ''}>
        <div><p>{t(job.kind)} <span className="muted">{t('jobNumber', { id: job.id })}</span></p><small className="muted"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time> · {t('tokens', { count: (job.input_tokens ?? 0) + (job.output_tokens ?? 0) })}</small>{job.error && <p className="error-text">{job.error}</p>}</div><StatusBadge status={job.status} />
      </li>)}</ul>
    </section>
    <Dialog open={dialog === 'continue' || dialog === 'ingest'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t(dialog === 'ingest' ? 'ingest' : 'continueTitle')}
      description={t('continueHint', { remaining, tokens: formatTokenEstimate(tokenEstimate) })}>
      {estimate.data?.mode === 'map_reduce' && <p>{t('mapReduceEstimate', {
        map: formatTokenEstimate(estimate.data.map_estimated_tokens), reduce: formatTokenEstimate(estimate.data.reduce_estimated_tokens),
      })}</p>}
      <p className="muted">{t('providerBilling')}</p>
      <Field label={t('runLimit')} hint={t('runLimitHint')}>
        <Input inputMode="numeric" value={maxTokens} onChange={event => setMaxTokens(event.target.value)} />
      </Field>
      <ErrorNotice error={ingest.error} />
      <Button disabled={pending || !maxTokensValid} onClick={() => ingest.mutate({
        ...input, resume: dialog === 'continue' || source.status !== 'ingested', max_tokens: parsedMaxTokens,
      }, { onSuccess: onQueued })}>{t(dialog === 'ingest' ? 'ingest' : 'continueConfirm')}</Button>
    </Dialog>
    <Dialog open={dialog === 'move'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('moveTitle')} description={t('moveHint')}>
      <Field label={t('folder')}><Select value={folder} onChange={event => setFolder(event.target.value)}><option value={folder}>{folder || t('rootFolder')}</option>{folders.data && <FolderOptions node={folders.data} />}</Select></Field>
      <Field label={t('newFolder')} hint={t('newFolderHint')}><Input value={folder} onChange={event => setFolder(event.target.value)} placeholder={t('folderExample')} /></Field>
      <ErrorNotice error={folders.error || move.error} /><Button disabled={pending || folder === source.folder} onClick={() => move.mutate({ ...input, folder: folder.trim() }, { onSuccess: onQueued })}>{t('move')}</Button>
    </Dialog>
    <ConfirmDialog open={dialog === 'reconvert'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('reconvertTitle')} description={t('reconvertHint')} confirmLabel={t('reconvert')} pending={pending} onConfirm={() => reconvert.mutate(input, { onSuccess: onQueued })}><ErrorNotice error={reconvert.error} /></ConfirmDialog>
    <ConfirmDialog open={dialog === 'restart'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('restartTitle')} description={t('restartHint')} confirmLabel={t('restartIngest')} pending={pending}
      onConfirm={() => ingest.mutate({ ...input, resume: false, max_tokens: null }, { onSuccess: onQueued })}><ErrorNotice error={ingest.error} /></ConfirmDialog>
    <ConfirmDialog open={dialog === 'delete'} onOpenChange={open => { if (!pending && !open) setDialog(null); }} title={t('deleteTitle')} description={t('deleteHint')} confirmLabel={t('delete')} pending={pending} onConfirm={() => remove.mutate(input, { onSuccess: onQueued })}><ErrorNotice error={remove.error} /></ConfirmDialog>
  </div>;
}

function CanonicalDocument({ brainId, source }: { brainId: string; source: Schema['SourceView'] }) {
  // A citation links here with #passage=start-end. Each navigation gives a new
  // passage object, so following the same link again scrolls to it again.
  const location = useLocation();
  const [locationKey, setLocationKey] = useState(location.key);
  const [passage, setPassage] = useState(() => parsePassageHash(location.hash));
  if (location.key !== locationKey) {
    setLocationKey(location.key);
    setPassage(parsePassageHash(location.hash));
  }
  // A Reconvert writes the Canonical Document again: reload its cached text.
  const { events } = useCurrentBrain();
  useReloadOnPageWrite(brainId, source.canonical_path, events);
  if (['queued', 'converting'].includes(source.status)) return <p className="muted">{t('convertingHint')}</p>;
  return <DocumentViewer brainId={brainId} path={source.canonical_path} passage={passage} />;
}
