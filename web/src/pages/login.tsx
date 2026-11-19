import { useState } from 'react';
import { BookOpen } from 'lucide-react';
import { useLogin } from '../api/hooks';
import { t } from '../i18n/en';
import { Button } from '../components/ui/button';
import { Field, Input } from '../components/ui/input';
import { ErrorNotice } from '../components/ui/feedback';
export function LoginPage() {
  const login = useLogin();
  const [password, setPassword] = useState('');
  return <main className="auth-page"><div className="auth-brand"><BookOpen size={24} strokeWidth={1.5} /><span>{t('appName')}</span></div>
    <section className="auth-card"><h1>{t('loginTitle')}</h1><p className="muted">{t('loginDescription')}</p>
      <form className="stack" onSubmit={event => { event.preventDefault(); login.mutate(password, { onSuccess: () => { setPassword(''); login.reset(); } }); }}>
        <Field label={t('password')}><Input type="password" autoComplete="current-password" autoFocus required value={password} onChange={e => setPassword(e.target.value)} /></Field>
        <ErrorNotice error={login.error} /><Button disabled={login.isPending}>{t(login.isPending ? 'signingIn' : 'signIn')}</Button>
      </form>
    </section><p className="auth-footer">{t('appDescription')}</p>
  </main>;
}
