import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { t } from '../i18n/en';
import { Button } from './ui/button';
export function CopyButton({ value }: { value: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'error'>('idle');
  async function copy() {
    try { await navigator.clipboard.writeText(value); setState('copied'); }
    catch { setState('error'); }
  }
  return <><Button type="button" variant="secondary" size="small" onClick={() => void copy()} aria-label={t('copy')}>
    {state === 'copied' ? <Check size={16} /> : <Copy size={16} />}{t(state === 'copied' ? 'copied' : 'copy')}
  </Button><span className={state === 'error' ? 'muted' : 'sr-only'} role="status">{state === 'error' ? t('copyFailed') : state === 'copied' ? t('copied') : ''}</span></>;
}
