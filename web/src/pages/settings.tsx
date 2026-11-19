import { useState } from 'react';
import { Check, Github, Plus, Server, Trash2 } from 'lucide-react';
import {
  useAgentSetup, useCreateToken, useDeleteProvider, useDeleteToken, useDisconnectGitHub,
  useGitHubAccount, useHealth, useProviderModels, useProviders, useSaveTaskSettings,
  useSetup, useTaskSettings, useTestProvider, useTokens,
} from '../api/hooks';
import type { Schema } from '../api/client';
import { t } from '../i18n/en';
import { useTheme } from '../theme/context';
import { Button } from '../components/ui/button';
import { Field, Input, Select } from '../components/ui/input';
import { ConfirmDialog, Dialog } from '../components/ui/dialog';
import { ErrorNotice, Loading, QueryError } from '../components/ui/feedback';
import { CopyButton } from '../components/copy-button';
import { GitHubConnect } from '../components/github-connect';
import { ProviderForm } from '../components/provider-form';

export function SettingsPage() {
  const { theme, setTheme } = useTheme();
  return <div className="page settings-page"><header className="page-heading"><h1>{t('settings')}</h1><p className="muted">{t('appDescription')}</p></header>
    <section className="settings-section"><div><h2>{t('theme')}</h2><p className="muted">{t('themeHint')}</p></div>
      <div className="theme-options" role="group" aria-label={t('theme')}>
        {(['paper', 'ink'] as const).map(value => <button key={value} className={`theme-option theme-${value}`} aria-pressed={theme === value} onClick={() => setTheme(value)}><span className="theme-preview" /><span>{t(value)}</span>{theme === value && <Check size={16} />}</button>)}
      </div></section>
    <GitHubSettings /><ProviderSettings /><TaskSettings /><TokenSettings /><AgentSettings /><WorkerSettings />
  </div>;
}
function GitHubSettings() {
  const account = useGitHubAccount();
  const setup = useSetup();
  const disconnect = useDisconnectGitHub();
  const [confirm, setConfirm] = useState(false);
  return <section className="settings-section"><div><h2>{t('githubAccount')}</h2><p className="muted">{t('githubAccountHint')}</p></div><div className="stack">
    {account.isPending ? <Loading /> : account.data ? <div className="setting-row"><div className="inline"><Github size={20} /><span>{t('connectedAs', { login: account.data.login })}</span></div><Button variant="secondary" onClick={() => setConfirm(true)}>{t('disconnect')}</Button></div> : <GitHubConnect deviceAvailable={setup.data?.github_app_configured ?? false} />}
    <ErrorNotice error={account.error || disconnect.error} />
    <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t('disconnectGitHubTitle')} description={t('disconnectGitHubHint')} confirmLabel={t('disconnect')} pending={disconnect.isPending} onConfirm={() => disconnect.mutate(undefined, { onSuccess: () => setConfirm(false) })} />
  </div></section>;
}
function ProviderSettings() {
  const providers = useProviders();
  const remove = useDeleteProvider();
  const test = useTestProvider();
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [tested, setTested] = useState<number | null>(null);
  return <section className="settings-section"><div><h2>{t('providers')}</h2><p className="muted">{t('providersDescription')}</p></div><div className="stack">
    {providers.isPending && <Loading />}{providers.data?.length === 0 && <p className="muted">{t('noProviders')}</p>}
    {providers.data?.map(provider => <div className="provider-row" key={provider.id}><div><strong>{provider.name}</strong><p className="mono muted">{provider.base_url}</p><small className="muted">{provider.has_api_key ? provider.api_key_last4 ? t('maskedKey', { last4: provider.api_key_last4 }) : t('maskedKeyHidden') : t('noKey')}</small></div>
      <div className="actions"><Button variant="secondary" size="small" disabled={test.isPending} onClick={() => { setTested(provider.id); test.mutate(provider.id); }}>{tested === provider.id && test.isPending ? t('loading') : t('testProvider')}</Button>
        <Button variant="ghost" size="icon" aria-label={`${t('remove')} ${provider.name}`} onClick={() => setDeleting(provider.id)}><Trash2 size={17} /></Button></div>
      {tested === provider.id && test.data && <p role="status" className={test.data.ok ? 'muted' : 'error-text'}>{test.data.message}</p>}
      {tested === provider.id && <ErrorNotice error={test.error} />}
    </div>)}
    <ErrorNotice error={providers.error || remove.error} /><div><Button variant="secondary" onClick={() => setAdding(true)}><Plus size={16} />{t('addProvider')}</Button></div>
    <Dialog open={adding} onOpenChange={setAdding} title={t('addProvider')} description={t('providerDescription')}><ProviderForm onAdded={() => setAdding(false)} /></Dialog>
    <ConfirmDialog open={deleting !== null} onOpenChange={open => { if (!open) setDeleting(null); }} title={t('deleteProviderTitle')} description={t('deleteProviderHint')} pending={remove.isPending} confirmLabel={t('remove')} onConfirm={() => { if (deleting !== null) remove.mutate(deleting, { onSuccess: () => setDeleting(null) }); }} />
  </div></section>;
}
function TaskSettings() {
  const tasks = useTaskSettings();
  const providers = useProviders();
  return <section className="settings-section"><div><h2>{t('tasks')}</h2><p className="muted">{t('tasksDescription')}</p></div>
    {tasks.isPending || providers.isPending ? <Loading /> : tasks.isError || providers.isError ? <QueryError error={tasks.error || providers.error} retry={() => { void tasks.refetch(); void providers.refetch(); }} /> : <TaskForm key={JSON.stringify(tasks.data)} tasks={tasks.data} providers={providers.data} />}
  </section>;
}
function TaskForm({ tasks, providers }: { tasks: Schema['TaskSettingsView']; providers: Schema['ProviderView'][] }) {
  const save = useSaveTaskSettings();
  const [ingest, setIngest] = useState(tasks.ingest.provider_id?.toString() ?? 'off');
  const [transcription, setTranscription] = useState(tasks.transcription.mode === 'local' ? 'local' : tasks.transcription.provider_id?.toString() ?? 'local');
  const [ingestModel, setIngestModel] = useState(tasks.ingest.model ?? '');
  const [transcriptionModel, setTranscriptionModel] = useState(tasks.transcription.model ?? '');
  const [saved, setSaved] = useState(false);
  const ingestModels = useProviderModels(ingest === 'off' ? undefined : Number(ingest));
  const transcriptionModels = useProviderModels(transcription === 'local' ? undefined : Number(transcription));
  function setting(provider: string, model: string): Schema['TaskSettingInput'] | null {
    if (provider === 'off') return null;
    if (provider === 'local') return { mode: 'local', model: model.trim() || null };
    return { mode: 'provider', provider_id: Number(provider), model: model.trim() };
  }
  return <form className="stack" onSubmit={e => { e.preventDefault(); save.mutate({ ingest: setting(ingest, ingestModel), transcription: setting(transcription, transcriptionModel) }, { onSuccess: () => setSaved(true) }); }}>
    <div className="form-grid"><Field label={t('ingest')}><Select value={ingest} onChange={e => { setIngest(e.target.value); setIngestModel(''); setSaved(false); }}><option value="off">{t('disabledIngest')}</option>{providers.map(provider => <option value={provider.id} key={provider.id}>{provider.name}</option>)}</Select></Field>
      <Field label={t('model')} hint={t('modelHint')}><Input list="ingest-models" required={ingest !== 'off'} disabled={ingest === 'off'} value={ingestModel} onChange={e => { setIngestModel(e.target.value); setSaved(false); }} /></Field>
      <datalist id="ingest-models">{ingestModels.data?.map(model => <option key={model} value={model} />)}</datalist>
      <Field label={t('transcription')}><Select value={transcription} onChange={e => { setTranscription(e.target.value); setTranscriptionModel(''); setSaved(false); }}><option value="local">{t('localWhisper')}</option>{providers.map(provider => <option value={provider.id} key={provider.id}>{provider.name}</option>)}</Select></Field>
      <Field label={t('model')} hint={t('modelHint')}><Input list="transcription-models" value={transcriptionModel} required={transcription !== 'local'} onChange={e => { setTranscriptionModel(e.target.value); setSaved(false); }} /></Field>
      <datalist id="transcription-models">{transcriptionModels.data?.map(model => <option key={model} value={model} />)}</datalist>
    </div><ErrorNotice error={save.error || ingestModels.error || transcriptionModels.error} /><div className="actions"><Button disabled={save.isPending}>{save.isPending ? t('loading') : t('save')}</Button>{saved && <span role="status" className="muted">{t('taskSaved')}</span>}</div>
  </form>;
}
function TokenSettings() {
  const tokens = useTokens();
  const create = useCreateToken();
  const remove = useDeleteToken();
  const [name, setName] = useState('');
  const [secret, setSecret] = useState('');
  const [deleting, setDeleting] = useState<number | null>(null);
  return <section className="settings-section"><div><h2>{t('apiTokens')}</h2><p className="muted">{t('apiTokensDescription')}</p></div><div className="stack">
    {tokens.isPending && <Loading />}{tokens.data?.length === 0 && <p className="muted">{t('noTokens')}</p>}
    {tokens.data?.map(token => <div className="setting-row" key={token.id}><div><strong>{token.name}</strong><p className="mono muted">{token.prefix}••••</p></div><Button variant="ghost" size="icon" aria-label={`${t('remove')} ${token.name}`} onClick={() => setDeleting(token.id)}><Trash2 size={17} /></Button></div>)}
    <form className="inline-form" onSubmit={event => { event.preventDefault(); create.mutate(name, { onSuccess: data => { setSecret(data.secret); setName(''); create.reset(); } }); }}>
      <Field label={t('tokenName')}><Input value={name} onChange={e => setName(e.target.value)} required /></Field><Button disabled={create.isPending || !!secret}>{create.isPending ? t('loading') : t('createToken')}</Button>
    </form>
    <ErrorNotice error={tokens.error || create.error || remove.error} />
    <Dialog open={!!secret} onOpenChange={open => { if (!open) setSecret(''); }} title={t('tokenSecret')} description={t('tokenOnce')}>
      <div className="code-block"><code>{secret}</code><CopyButton value={secret} /></div><Button onClick={() => setSecret('')}>{t('dismissSecret')}</Button>
    </Dialog>
    <ConfirmDialog open={deleting !== null} onOpenChange={open => { if (!open) setDeleting(null); }} title={t('revokeTokenTitle')} description={t('revokeTokenHint')} confirmLabel={t('remove')} pending={remove.isPending} onConfirm={() => { if (deleting !== null) remove.mutate(deleting, { onSuccess: () => setDeleting(null) }); }} />
  </div></section>;
}
function AgentSettings() {
  const setup = useAgentSetup();
  return <section className="settings-section"><div><h2>{t('agentSetup')}</h2><p className="muted">{t('agentDescription')}</p></div><div className="stack">
    {setup.isPending && <Loading />}<ErrorNotice error={setup.error} />
    {setup.data && <>{([
      [t('mcpUrl'), setup.data.mcp_url], [t('mcpCommand'), setup.data.mcp_add_command], [t('skillCommand'), setup.data.skill_install_command],
    ] as const).map(([label, command]) => <div key={label}><h3>{label}</h3><div className="code-block"><code>{command}</code><CopyButton value={command} /></div></div>)}
      {!!setup.data.clone_commands.length && <h3>{t('cloneCommands')}</h3>}{setup.data.clone_commands.map(clone => <div key={clone.brain_id}><p>{clone.name}</p><div className="code-block"><code>{clone.command}</code><CopyButton value={clone.command} /></div></div>)}
    </>}
  </div></section>;
}
function WorkerSettings() {
  const health = useHealth();
  return <section className="settings-section"><h2>{t('worker')}</h2><div className="stack">
    {health.isPending && <Loading />}<ErrorNotice error={health.error} />{health.data && <>
      <div className="inline"><Server size={18} /><strong>{t(health.data.worker.online ? 'workerOnline' : 'workerOffline')}</strong></div>
      {!health.data.worker.online && <p className="muted">{t('workerHint')}</p>}
      {health.data.worker.last_seen_at && <p className="muted">{t('lastSeen', { time: new Date(health.data.worker.last_seen_at).toLocaleString('en') })}</p>}
    </>}
  </div></section>;
}
