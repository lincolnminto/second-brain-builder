import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { Activity, BookOpen, LogOut, Menu, Moon, Plus, Search, Sun, X } from 'lucide-react';
import { useAuth, useBrains, useJobEvents, useLogout } from '../api/hooks';
import { t } from '../i18n/en';
import { useTheme } from '../theme/context';
import { Button } from '../components/ui/button';
import { Select } from '../components/ui/input';
import { ErrorNotice } from '../components/ui/feedback';
import { BrainContext } from './context';
import { navigation } from './navigation';
import { FolderTree } from './folder-tree';
import { ActivityRail } from './activity';
import { CommandPalette } from './command-palette';

export function AppShell() {
  const { brainId: routeBrainId } = useParams();
  const brains = useBrains();
  const brainId = routeBrainId ?? brains.data?.[0]?.id ?? '';
  const brain = brains.data?.find(brain => brain.id === brainId);
  const { events, connected } = useJobEvents(brainId || undefined);
  const auth = useAuth();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const { theme, setTheme } = useTheme();
  const [rail, setRail] = useState(() => window.innerWidth >= 1200);
  const [menu, setMenu] = useState(false);
  const [palette, setPalette] = useState(false);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPalette(open => !open); }
      if (event.key === 'Escape') setMenu(false);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  useEffect(() => { if (params.has('activity')) setRail(true); }, [params]);
  const currentLabel = location.pathname.includes('/sources/') ? 'source' : navigation.find(item => item.path && (item.path === '/settings' ? location.pathname === item.path : location.pathname.startsWith(`/brains/${brainId}${item.path}`)))?.label ?? 'home';
  return <BrainContext.Provider value={{ brainId, brain, events, connected }}>
    <a className="skip-link" href="#main-content">{t('skipContent')}</a>
    <div className={`app-shell ${rail ? 'with-rail' : ''}`}>
      {menu && <button className="sidebar-scrim" aria-label={t('closeMenu')} onClick={() => setMenu(false)} />}
      <aside className={`sidebar ${menu ? 'is-open' : ''}`} aria-label={t('brains')}><Link className="brand" to="/"><BookOpen size={25} strokeWidth={1.4} /><span>{t('appName')}</span></Link>
        <label className="brain-switcher"><span className="sr-only">{t('brain')}</span><Select value={brainId} onChange={e => { navigate(`/brains/${encodeURIComponent(e.target.value)}`); setMenu(false); }}>
          {!brainId && <option value="">{t('noBrain')}</option>}{brains.data?.map(brain => <option value={brain.id} key={brain.id}>{brain.name}</option>)}
        </Select></label><ErrorNotice error={brains.error} />
        <nav className="main-nav">{navigation.map(item => <NavLink end={item.path === ''} key={item.label} to={item.path === '/settings' ? item.path : `/brains/${encodeURIComponent(brainId)}${item.path}`} onClick={() => setMenu(false)}><item.icon size={18} strokeWidth={1.6} /><span>{t(item.label)}</span>{item.label === 'search' && <kbd>⌘K</kbd>}</NavLink>)}</nav>
        {brainId && <FolderTree brainId={brainId} onNavigate={() => setMenu(false)} />}
        <div className="sidebar-footer"><Button asChild variant="ghost"><Link to="/onboarding?new=1"><Plus size={17} />{t('newBrain')}</Link></Button>
          <div className="setting-row"><Button variant="ghost" size="icon" aria-label={t(theme === 'paper' ? 'ink' : 'paper')} onClick={() => setTheme(theme === 'paper' ? 'ink' : 'paper')}>{theme === 'paper' ? <Moon size={18} /> : <Sun size={18} />}</Button>
            {auth.data?.login_required && <Button variant="ghost" size="icon" aria-label={t('signOut')} disabled={logout.isPending} onClick={() => logout.mutate()}><LogOut size={18} /></Button>}
          </div><ErrorNotice error={logout.error} />
        </div>
      </aside>
      <div className="workspace"><header className="topbar"><div className="inline"><Button className="mobile-menu" variant="ghost" size="icon" aria-label={t(menu ? 'closeMenu' : 'openMenu')} aria-expanded={menu} onClick={() => setMenu(!menu)}>{menu ? <X size={19} /> : <Menu size={19} />}</Button><span className="breadcrumb">{brain?.name ?? t('appName')}<span>/</span>{t(currentLabel)}</span></div>
        <div className="inline"><Button variant="ghost" size="icon" aria-label={t('commandPalette')} onClick={() => setPalette(true)}><Search size={18} /></Button><Button variant="ghost" size="icon" aria-pressed={rail} aria-label={t(rail ? 'hideActivity' : 'showActivity')} onClick={() => setRail(!rail)}><Activity size={18} /></Button></div>
      </header><main id="main-content" tabIndex={-1}><Outlet /></main></div>
      {rail && <ActivityRail />}
    </div>
    {palette && <CommandPalette brainId={brainId} open={palette} onOpenChange={setPalette} />}
  </BrainContext.Provider>;
}
