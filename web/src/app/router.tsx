import { lazy, Suspense, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { LOGIN_REQUIRED } from '../api/client';
import { useAuth, useBrains, useSetup } from '../api/hooks';
import { Loading, QueryError } from '../components/ui/feedback';
import { LoginPage } from '../pages/login';
import { OnboardingPage } from '../pages/onboarding';
import { SettingsPage } from '../pages/settings';
import { GraphSkeleton } from '../features/graph/skeleton';
import { AppShell } from './shell';

const BrainPlaceholder = lazy(() => import('../features/brain').then(module => ({ default: module.BrainPlaceholder })));
const SourcePlaceholder = lazy(() => import('../features/source').then(module => ({ default: module.SourcePlaceholder })));
const WikiPlaceholder = lazy(() => import('../features/wiki').then(module => ({ default: module.WikiPlaceholder })));
const SearchPlaceholder = lazy(() => import('../features/search').then(module => ({ default: module.SearchPlaceholder })));
const GraphPlaceholder = lazy(() => import('../features/graph/placeholder').then(module => ({ default: module.GraphPlaceholder })));

function RouteSuspense({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Loading />}>{children}</Suspense>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const cache = useQueryClient();
  useEffect(() => {
    const login = () => {
      void cache.cancelQueries().then(() => {
        cache.removeQueries({ predicate: query => query.queryKey[0] !== 'auth' });
        cache.setQueryData(['auth'], { login_required: true, authenticated: false });
      });
    };
    window.addEventListener(LOGIN_REQUIRED, login);
    return () => window.removeEventListener(LOGIN_REQUIRED, login);
  }, [cache]);
  if (auth.isPending) return <Loading />;
  if (auth.isError) return <QueryError error={auth.error} retry={() => void auth.refetch()} />;
  if (auth.data.login_required && !auth.data.authenticated) return <LoginPage />;
  return children;
}
function StartPage() {
  const setup = useSetup();
  const brains = useBrains();
  if (setup.isPending || brains.isPending) return <Loading />;
  if (setup.isError || brains.isError) return <QueryError error={setup.error || brains.error} retry={() => { void setup.refetch(); void brains.refetch(); }} />;
  if (!setup.data.github_connected || !brains.data.length) return <Navigate replace to="/onboarding" />;
  return <Navigate replace to={`/brains/${encodeURIComponent(brains.data[0]!.id)}`} />;
}
export function AppRouter() {
  return <BrowserRouter><AuthGate><Routes>
    <Route path="/" element={<StartPage />} />
    <Route path="/onboarding" element={<OnboardingPage />} />
    <Route element={<AppShell />}><Route path="/settings" element={<SettingsPage />} /></Route>
    <Route path="/brains/:brainId" element={<AppShell />}>
      <Route index element={<RouteSuspense><BrainPlaceholder /></RouteSuspense>} />
      <Route path="sources/:slug" element={<RouteSuspense><SourcePlaceholder /></RouteSuspense>} />
      <Route path="wiki/*" element={<RouteSuspense><WikiPlaceholder /></RouteSuspense>} />
      <Route path="search" element={<RouteSuspense><SearchPlaceholder /></RouteSuspense>} />
      <Route path="graph" element={<Suspense fallback={<GraphSkeleton />}><GraphPlaceholder /></Suspense>} />
    </Route>
    <Route path="*" element={<Navigate replace to="/" />} />
  </Routes></AuthGate></BrowserRouter>;
}
