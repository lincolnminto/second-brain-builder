import { Fragment } from 'react';
import { Activity, FileText, FolderOpen, Radio } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { useCurrentBrain } from '../../app/context';
import { useFolders, useJobs, useSources } from '../../api/hooks';
import type { Schema } from '../../api/client';
import { DropZone } from '../../components/drop-zone';
import { StatusBadge } from '../../components/ui/badge';
import { SourceListStatus } from './source-status';
import { Field, Select } from '../../components/ui/input';
import { Loading, QueryError } from '../../components/ui/feedback';
import { t } from './strings';
import './brain.css';

function FolderOptions({ folders, depth = 0 }: { folders: Schema['FolderNode'][]; depth?: number }) {
  return folders.map(folder => <Fragment key={folder.path}>
    <option value={folder.path}>{'— '.repeat(depth)}{folder.name}</option>
    <FolderOptions folders={folder.children ?? []} depth={depth + 1} />
  </Fragment>);
}

export function BrainPlaceholder() {
  const { brainId, brain, events, connected } = useCurrentBrain();
  const [params, setParams] = useSearchParams();
  const folder = params.get('folder') ?? '';
  const folders = useFolders(brainId);
  const sources = useSources(brainId, folder || undefined);
  const jobs = useJobs(brainId);
  const recent = [...(jobs.data ?? [])].sort((a, b) => b.id - a.id).slice(0, 5);
  const root = `/brains/${encodeURIComponent(brainId)}`;
  return <div className="page brain-home">
    <header className="page-heading"><h1>{brain?.name ?? t('title')}</h1><p className="muted">{t('intro')}</p></header>
    <div className="brain-folder-bar">
      <Field label={t('folder')} hint={t('folderHint')}><Select value={folder} onChange={event => {
        const next = new URLSearchParams(params);
        if (event.target.value) next.set('folder', event.target.value); else next.delete('folder');
        setParams(next);
      }}><option value="">{t('allSources')}</option>
        {folder && !folders.data && <option value={folder}>{folder}</option>}
        <FolderOptions folders={folders.data?.children ?? []} />
      </Select></Field>
      <span className="brain-destination muted"><FolderOpen size={16} />{t('addingTo')} <span className="mono">{folder || t('rootFolder')}</span></span>
    </div>
    {folders.isError && <QueryError error={folders.error} retry={() => void folders.refetch()} />}
    <div className="brain-drop"><DropZone key={`${brainId}:${folder}`} brainId={brainId} folder={folder} disabled={brain?.status !== 'ready'} /></div>
    <section className="brain-sources" aria-labelledby="sources-heading">
      <header className="brain-section-heading"><h2 id="sources-heading">{t('sources')}</h2><span className="muted">{sources.data?.length ?? 0}</span></header>
      {sources.isPending && <Loading />}
      {sources.isError && <QueryError error={sources.error} retry={() => void sources.refetch()} />}
      {sources.data?.length === 0 && <div className="brain-empty"><FileText size={24} strokeWidth={1.2} /><h3>{t(folder ? 'emptyFolder' : 'emptyTitle')}</h3><p className="muted">{t('emptyHint')}</p></div>}
      <ul className="brain-source-list">{sources.data?.map(source => <li key={source.slug}>
        <FileText size={20} strokeWidth={1.3} className="muted" />
        <div className="brain-source-title"><Link to={`${root}/sources/${encodeURIComponent(source.slug)}`}>{source.title || source.slug}</Link>
          <p className="mono muted">{source.folder || t('rootFolder')}{source.media_type ? ` / ${source.media_type}` : ''}</p></div>
        <SourceListStatus brainId={brainId} source={source} />
      </li>)}</ul>
    </section>
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
