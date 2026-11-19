import { useEffect, useRef, useState } from 'react';
import { Check, Github } from 'lucide-react';
import { useConnectPat, useGitHubAccount, usePollDeviceFlow, useStartDeviceFlow } from '../api/hooks';
import type { Schema } from '../api/client';
import { t } from '../i18n/en';
import { Button } from './ui/button';
import { Field, Input } from './ui/input';
import { ErrorNotice } from './ui/feedback';
import { CopyButton } from './copy-button';

export function GitHubConnect({ deviceAvailable = true, onConnected }: { deviceAvailable?: boolean; onConnected?: () => void }) {
  const account = useGitHubAccount();
  const start = useStartDeviceFlow();
  const pat = useConnectPat();
  const { mutateAsync: poll } = usePollDeviceFlow();
  const [token, setToken] = useState('');
  const [session, setSession] = useState<(Schema['DeviceFlowStart'] & { expiresAt: number }) | null>(null);
  const [pollError, setPollError] = useState<Error | null>(null);
  const connected = useRef(onConnected);
  connected.current = onConnected;
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (Date.now() >= session.expiresAt) { setPollError(new Error(t('deviceExpired'))); setSession(null); return; }
      try {
        const response = await poll();
        if (cancelled) return;
        if (response.status === 'connected') { setSession(null); connected.current?.(); return; }
        if (response.status === 'expired' || response.status === 'denied') {
          setPollError(new Error(t(response.status === 'expired' ? 'deviceExpired' : 'deviceDenied')));
          setSession(null); return;
        }
        timer = setTimeout(() => void tick(), Math.max(1, response.interval ?? session.interval) * 1000);
      } catch (error) {
        if (!cancelled) { setPollError(error instanceof Error ? error : new Error(t('connectionError'))); setSession(null); }
      }
    };
    timer = setTimeout(() => void tick(), Math.max(1, session.interval) * 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [session, poll]);
  if (account.data) return <div className="notice"><Check size={20} /><span>{t('connectedAs', { login: account.data.login })}</span></div>;
  return <div className="stack">
    {deviceAvailable ? <>
      <Button variant="secondary" disabled={start.isPending || !!session} onClick={() => {
        setPollError(null);
        start.mutate(undefined, { onSuccess: data => setSession({ ...data, expiresAt: Date.now() + data.expires_in * 1000 }) });
      }}><Github size={18} />{start.isPending ? t('loading') : t('deviceFlow')}</Button>
      {session && <div className="device-session stack"><span className="muted">{t('deviceCode')}</span>
        <div className="code-row"><strong className="device-code">{session.user_code}</strong><CopyButton value={session.user_code} /></div>
        <p className="muted">{t('deviceHint')}</p>
        <Button asChild><a href={session.verification_uri} target="_blank" rel="noopener noreferrer">{t('openGitHub')}</a></Button>
        <p role="status" className="muted">{t('waitingForGitHub')}</p>
      </div>}
    </> : <p className="muted">{t('deviceNotConfigured')}</p>}
    <ErrorNotice error={start.error || pollError} />
    <details open={!deviceAvailable}><summary>{t('patAlternative')}</summary>
      <form className="stack form-inset" onSubmit={event => {
        event.preventDefault();
        pat.mutate(token, { onSuccess: () => { setToken(''); setSession(null); pat.reset(); connected.current?.(); } });
      }}>
        <Field label={t('pat')} hint={t('patHint')}><Input type="password" value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" /></Field>
        <ErrorNotice error={pat.error} />
        <Button disabled={pat.isPending || !token.trim()}>{pat.isPending ? t('loading') : t('connect')}</Button>
      </form>
    </details>
  </div>;
}
