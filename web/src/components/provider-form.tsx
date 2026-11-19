import { useState } from 'react';
import { useCreateProvider } from '../api/hooks';
import type { Schema } from '../api/client';
import { t } from '../i18n/en';
import { Button } from './ui/button';
import { Field, Input, Select } from './ui/input';
import { ErrorNotice } from './ui/feedback';

export function ProviderForm({ onAdded }: { onAdded?: (provider: Schema['ProviderView']) => void }) {
  const create = useCreateProvider();
  const [kind, setKind] = useState<Schema['ProviderKind']>('openai');
  const [key, setKey] = useState('');
  return <form className="stack" onSubmit={event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    create.mutate({ name: String(data.get('name')), kind, base_url: String(data.get('base_url')), api_key: key }, {
      onSuccess: provider => { setKey(''); create.reset(); onAdded?.(provider); },
    });
  }}>
    <Field label={t('providerName')}><Input name="name" required /></Field>
    <Field label={t('providerKind')}><Select value={kind} onChange={e => setKind(e.target.value as Schema['ProviderKind'])}>
      <option value="openai">{t('openaiCompatible')}</option><option value="anthropic">{t('anthropicCompatible')}</option>
    </Select></Field>
    <Field label={t('baseUrl')}><Input name="base_url" type="url" required placeholder={kind === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com'} /></Field>
    <Field label={t('apiKey')} hint={t('optionalKey')}><Input type="password" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" /></Field>
    <ErrorNotice error={create.error} />
    <Button disabled={create.isPending}>{create.isPending ? t('loading') : t('addProvider')}</Button>
  </form>;
}
