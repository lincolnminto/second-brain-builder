import { afterEach, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { useLocation } from 'react-router';
import userEvent from '@testing-library/user-event';
import { CommandPalette } from './command-palette';
import { json, mockApi, renderApp } from '../test/helpers';
function Location() { return <div data-testid="location">{useLocation().pathname}</div>; }
afterEach(() => vi.unstubAllGlobals());
it('searches the current Brain, selects a page with the keyboard, and closes the dialog', async () => {
  mockApi((_request, path) => path.endsWith('/search') ? json([{ path: 'wiki/concepts/attention.md', title: 'Attention', type: 'concept', snippet: '**Attention**', score: 1 }]) : undefined);
  const user = userEvent.setup(); const close = vi.fn();
  renderApp(<><CommandPalette brainId="brain-1" open onOpenChange={close} /><Location /></>);
  await user.type(screen.getByRole('combobox'), 'attention');
  await screen.findByRole('option', { name: /Attention/ });
  await user.keyboard('{Enter}');
  expect(close).toHaveBeenCalledWith(false);
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/brains/brain-1/wiki/wiki/concepts/attention.md'));
});
