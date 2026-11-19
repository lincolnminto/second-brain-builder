import { AlertCircle, LoaderCircle } from 'lucide-react';
import { t } from '../../i18n/en';
import { Button } from './button';
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="notice notice-error"><AlertCircle size={18} /><span>{error instanceof Error ? error.message : t('connectionError')}</span></div>;
}
export function Loading() { return <div role="status" className="loading"><LoaderCircle className="spin" size={20} />{t('loading')}</div>; }
export function QueryError({ error, retry }: { error: unknown; retry: () => void }) {
  return <div className="stack"><ErrorNotice error={error} /><div><Button variant="secondary" onClick={retry}>{t('retry')}</Button></div></div>;
}
