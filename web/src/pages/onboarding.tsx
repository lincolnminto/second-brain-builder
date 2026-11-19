import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { BookOpen, Check, Github } from 'lucide-react';
import { useBrains, useCreateBrain, useGitHubRepos, useSetup, useGitHubInstallUrl } from '../api/hooks';
import { ApiError, type Schema } from '../api/client';
import { t, type MessageKey } from '../i18n/en';
import { Button } from '../components/ui/button';
import { Field, Input, Select } from '../components/ui/input';
import { ErrorNotice, Loading, QueryError } from '../components/ui/feedback';
import { GitHubConnect } from '../components/github-connect';
import { ProviderForm } from '../components/provider-form';
import { DropZone } from '../components/drop-zone';

const steps: MessageKey[] = ['connectGitHub', 'createBrain', 'addProvider', 'firstSource'];
export function OnboardingPage() {
  const setup = useSetup();
  if (setup.isPending) return <Loading />;
  if (setup.isError) return <QueryError error={setup.error} retry={() => void setup.refetch()} />;
  return <OnboardingWizard setup={setup.data} />;
}
function OnboardingWizard({ setup }: { setup: Schema['SetupStatus'] }) {
  const [params] = useSearchParams();
  const brains = useBrains();
  const [step, setStep] = useState(setup.github_connected ? setup.brain_count && !params.has('new') ? 2 : 1 : 0);
  const [brainId, setBrainId] = useState('');
  const activeBrain = brains.data?.find(brain => brain.id === brainId) ?? brains.data?.[0];
  const [sourceAdded, setSourceAdded] = useState(false);
  return <main className="onboarding"><header className="onboarding-brand"><BookOpen size={24} strokeWidth={1.4} /><Link to="/">{t('appName')}</Link></header>
    <div className="onboarding-layout"><aside className="onboarding-intro"><h1>{t('welcome')}</h1><p className="muted">{t('onboardingDescription')}</p>
      <ol className="step-list">{steps.map((label, index) => <li key={label} aria-current={step === index ? 'step' : undefined} className={step === index ? 'active' : ''}>
        <span className="step-number">{step > index ? <Check size={16} /> : index + 1}</span>{t(label)}</li>)}</ol>
    </aside><section className="onboarding-card"><p className="muted">{t('stepOf', { current: step + 1, total: 4 })}</p><h2>{t(steps[step]!)}</h2>
      {step === 0 && <div className="stack"><p className="muted">{t('githubDescription')}</p><GitHubConnect deviceAvailable={setup.github_app_configured} onConnected={() => setStep(1)} />
        {setup.github_connected && <Button onClick={() => setStep(1)}>{t('continue')}</Button>}
      </div>}
      {step === 1 && <BrainStep connected={setup.github_connected} onCreated={id => { setBrainId(id); setStep(2); }} />}
      {step === 2 && <div className="stack"><p className="muted">{t('providerDescription')}</p><ProviderForm onAdded={() => setStep(3)} />
        <Button variant="ghost" onClick={() => setStep(3)}>{t('skipProvider')}</Button></div>}
      {step === 3 && <div className="stack">
        <ErrorNotice error={brains.error} />
        {activeBrain?.status === 'creating' && <div className="notice"><div><strong>{t('brainCreating')}</strong><p className="muted">{t('brainCreatingHint')}</p></div></div>}
        {activeBrain?.status === 'error' && <div role="alert" className="notice notice-error">{t('brainFailed')}</div>}
        {activeBrain && <DropZone brainId={activeBrain.id} disabled={activeBrain.status !== 'ready'} onAdded={() => setSourceAdded(true)} />}
        {activeBrain && <Button asChild variant={sourceAdded ? 'default' : 'secondary'}><Link to={`/brains/${encodeURIComponent(activeBrain.id)}`}>{t('openBrain')}</Link></Button>}
      </div>}
      {step > 0 && <Button variant="ghost" className="back-button" onClick={() => setStep(step - 1)}>{t('back')}</Button>}
    </section></div>
  </main>;
}
function BrainStep({ onCreated, connected }: { onCreated: (id: string) => void; connected: boolean }) {
  const [mode, setMode] = useState<'new' | 'existing' | 'brain'>('new');
  const [repo, setRepo] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const repos = useGitHubRepos(connected && mode === 'existing');
  const brains = useBrains();
  const install = useGitHubInstallUrl(connected && mode === 'existing');
  const create = useCreateBrain();
  const selected = repos.data?.find(item => item.full_name === repo);
  const publicWarning = (selected && !selected.private) || (create.error instanceof ApiError && create.error.body.code === 'public_repo');
  return <form className="stack" onSubmit={event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (mode === 'brain') { onCreated(String(data.get('brain'))); return; }
    create.mutate({ name: String(data.get('name')), language: String(data.get('language')), ...(mode === 'new' ? { new_repo_name: String(data.get('repo')) } : { existing_repo: repo }), confirm_public: confirmed }, { onSuccess: data => onCreated(data.brain.id) });
  }}>
    <p className="muted">{t('repoDescription')}</p>
    <Field label={t('repository')}><Select value={mode} onChange={e => { setMode(e.target.value as typeof mode); setConfirmed(false); create.reset(); }}>
      <option value="new">{t('newRepo')}</option><option value="existing">{t('existingRepo')}</option>
      {!!brains.data?.length && <option value="brain">{t('useBrain')}</option>}
    </Select></Field>
    {mode === 'brain' ? <Field label={t('chooseBrain')}><Select name="brain" required>{brains.data?.map(brain => <option key={brain.id} value={brain.id}>{brain.name}</option>)}</Select></Field> : <>
      <Field label={t('brainName')}><Input name="name" required /></Field>
      <Field label={t('language')}><Input name="language" defaultValue="en" required /></Field>
      {mode === 'new' ? <Field label={t('repoName')}><Input name="repo" pattern="[A-Za-z0-9_.-]+" required /></Field> : <>
        <Field label={t('repository')}><Select required value={repo} onChange={e => { setRepo(e.target.value); setConfirmed(false); create.reset(); }}><option value="">{t('chooseRepo')}</option>
          {repos.data?.filter(repo => !repo.brain_id).map(repo => <option key={repo.full_name} value={repo.full_name}>{repo.full_name} ({t(repo.private ? 'privateRepo' : 'publicRepo')})</option>)}
        </Select></Field>
        {repos.isPending && <Loading />}{repos.data?.length === 0 && <p className="muted">{t('noRepos')}</p>}
        <ErrorNotice error={repos.error} />{install.data && <a href={install.data.url} target="_blank" rel="noopener noreferrer"><Github size={16} /> {t('manageRepoAccess')}</a>}
        {publicWarning && <div className="notice notice-warning stack"><p>{t('publicRepoWarning')}</p><label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />{t('confirmPublic')}</label></div>}
      </>}
    </>}
    <ErrorNotice error={create.error} /><Button disabled={create.isPending || (!!publicWarning && !confirmed)}>{create.isPending ? t('loading') : t(mode === 'brain' ? 'continue' : 'createBrain')}</Button>
  </form>;
}
