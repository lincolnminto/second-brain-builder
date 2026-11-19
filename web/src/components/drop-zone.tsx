import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Check, FilePlus2 } from 'lucide-react';
import { ApiError } from '../api/client';
import { useUploadSource } from '../api/hooks';
import { t } from '../i18n/en';
import { Button } from './ui/button';
import { ErrorNotice } from './ui/feedback';
import { ConfirmDialog } from './ui/dialog';

export function DropZone({ brainId, disabled = false, onAdded, folder = '' }: {
  brainId: string; disabled?: boolean; folder?: string; onAdded?: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const upload = useUploadSource();
  const conflict = upload.error instanceof ApiError ? upload.error.body : null;
  const [quotaOpen, setQuotaOpen] = useState(false);
  useEffect(() => () => abort.current?.abort(), []);
  function addFile(next: File, confirmOverQuota = false) {
    if (disabled || upload.isPending) return;
    setFile(next); setProgress(0);
    abort.current = new AbortController();
    upload.mutate({ brainId, file: next, folder, confirmOverQuota, onProgress: setProgress, signal: abort.current.signal }, {
      onSuccess: () => { setFile(null); setQuotaOpen(false); onAdded?.(); },
      onError: error => { if (error instanceof ApiError && error.body.code === 'quota_exceeded') setQuotaOpen(true); },
    });
  }
  return <div className="stack">
    <div className={`drop-zone ${dragging ? 'dragging' : ''} ${disabled ? 'disabled' : ''}`}
      onDragOver={event => { event.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)} onDrop={event => {
        event.preventDefault(); setDragging(false);
        const next = event.dataTransfer.files[0]; if (next) addFile(next);
      }}>
      <FilePlus2 size={32} strokeWidth={1.3} /><h2>{t('dropTitle')}</h2><p className="muted">{t('dropHint')}</p>
      <Button variant="secondary" onClick={() => input.current?.click()} disabled={disabled || upload.isPending}>{t('chooseFile')}</Button>
      <input ref={input} type="file" className="sr-only" tabIndex={-1} aria-label={t('chooseFile')} disabled={disabled || upload.isPending} onChange={event => {
        const next = event.target.files?.[0]; if (next) addFile(next); event.target.value = '';
      }} />
      <div className="format-chips" aria-label={t('formats')}>{t('formats').split(', ').map(format => <span key={format}>{format}</span>)}</div>
    </div>
    {upload.isPending && <div role="status" className="stack compact"><span>{t('uploading', { percent: progress })}</span><progress value={progress} max={100} /></div>}
    {upload.isSuccess && <div className="notice"><Check size={18} /><div><strong>{t('uploadQueued')}</strong><p className="muted">{t('uploadSuccessHint')}</p></div></div>}
    {conflict?.code === 'duplicate_source' ? <div role="alert" className="notice"><div><p>{t('duplicateSource')}</p>
      {conflict.existing_source && <Link to={`/brains/${encodeURIComponent(brainId)}/sources/${encodeURIComponent(conflict.existing_source.slug)}`}>{t('viewExistingSource')}</Link>}
      {conflict.pending_job && <Link to={`/brains/${encodeURIComponent(brainId)}?activity=1`}>{t('viewPendingJob')}</Link>}
    </div></div> : conflict?.code !== 'quota_exceeded' && <ErrorNotice error={upload.error} />}
    <ConfirmDialog open={quotaOpen} onOpenChange={setQuotaOpen} title={t('quotaTitle')} description={t('quotaHint')} confirmLabel={t('uploadAnyway')} pending={upload.isPending} onConfirm={() => { if (file) addFile(file, true); }}>
      <p className="muted">{t('quotaUsage', { used: conflict?.used_bytes ?? 0, upload: conflict?.upload_bytes ?? 0, quota: conflict?.quota_bytes ?? 0 })}</p>
    </ConfirmDialog>
  </div>;
}
