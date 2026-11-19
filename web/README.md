# Web foundation

A local, static React client for Second Brain Builder. UI copy is English and lives
in `src/i18n/en.ts`. Geist, Geist Mono, Newsreader, and Lucide icons are bundled;
no remote fonts, remote document images, analytics, or runtime CDN dependencies.

## Commands

Run from the repository root:

```sh
pnpm --dir web install
pnpm --dir web gen:api
pnpm --dir web dev
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

The development server proxies `/api` and `/mcp` to `http://127.0.0.1:8000`.
The production build is `web/dist`, served by the FastAPI application, including
nested client routes. Node 22.12+ or 24 and pnpm are required. The pnpm workspace
configuration explicitly allows esbuild's installation script.

`gen:api` runs `uv run python scripts/export_openapi.py web/src/api/openapi.json`
from the repository root, then `openapi-typescript` to produce `src/api/schema.d.ts`.
The exporter imports `create_app`, uses isolated temporary storage with an ephemeral
in-memory key, closes its database connections, and never opens instance credentials.
Both generated files belong in source control. Never edit them by hand.

## Ownership and routes

```text
web/
  package.json, pnpm-lock.yaml, pnpm-workspace.yaml
  vite.config.ts, tsconfig.json, eslint.config.js, components.json
  index.html
  src/
    api/           generated OpenAPI, typed HTTP/XHR clients, QueryClient
      hooks/       system, github, providers, brains, sources, pages, jobs, agents, events
    app/           router, auth gate, shell, Brain context, Activity, Folder tree, command palette
    components/    shared forms, DropZone, CopyButton, SafeMarkdown, FeaturePlaceholder
      ui/          shadcn-style Radix primitives, fields, badges, feedback
    features/
      brain/       BrainPlaceholder
      source/      SourcePlaceholder
      wiki/        WikiPlaceholder
      search/      SearchPlaceholder
      graph/       GraphPlaceholder
    pages/         LoginPage, OnboardingPage, SettingsPage
    theme/         ThemeProvider, useTheme
    i18n/          en.ts catalog and t()
    test/          generated-type fixtures and transport helpers
    main.tsx, styles.css
```

Later screen tasks should replace only their exported placeholder in
`src/features/<feature>/index.tsx` and add files inside that feature directory.
Keep the export names so the existing router does not need changes. Feature code
must not import another feature's implementation. Shared pieces live outside them.

| URL | Component |
| --- | --- |
| `/` | Checks setup and opens onboarding or the first Brain |
| `/onboarding` | Four-step setup, resumable from server setup state |
| `/onboarding?new=1` | Creates another Brain with the connected GitHub account |
| `/brains/:brainId` | `BrainPlaceholder` |
| `/brains/:brainId/sources/:slug` | `SourcePlaceholder` |
| `/brains/:brainId/wiki/*` | `WikiPlaceholder`; `*` is the full Brain-relative path |
| `/brains/:brainId/search` | `SearchPlaceholder` |
| `/brains/:brainId/graph` | `GraphPlaceholder` |
| `/settings` | `SettingsPage` |

For example, `wiki/entities/heron.md` routes to
`/brains/<id>/wiki/wiki/entities/heron.md`; the first `wiki` selects the screen and
the wildcard retains the full API path. The Source id is its slug. Folder links
set `?folder=<path>` on Brain home. `?activity=1` opens the Activity rail.

## Shared API and hooks

Import hooks from `src/api/hooks`. Every route group has typed query/mutation hooks;
input and response types come from `Schema` in `src/api/client.ts`.

- System: `useAuth`, `useLogin`, `useLogout`, `useSetup`, `useHealth`.
- GitHub: `useGitHubAccount`, `useGitHubRepos`, `useGitHubInstallUrl`,
  `useStartDeviceFlow`, `usePollDeviceFlow`, `useConnectPat`, `useDisconnectGitHub`.
- Brains: `useBrains`, `useBrain`, `useCreateBrain`, `useDisconnectBrain`,
  `useSyncBrain`, `useLfsUsage`.
- Providers: `useProviders`, `useCreateProvider`, `useUpdateProvider`,
  `useDeleteProvider`, `useTestProvider`, `useProviderModels`, `useTaskSettings`,
  `useSaveTaskSettings`.
- Sources: `useFolders`, `useSources`, `useSource`, `useUploadSource`,
  `useMoveSource`, `useDeleteSource`, `useReconvertSource`, `useIngestSource`.
- Pages: `usePages`, `usePage`, `useGraph`, `useSearch`.
- Jobs: `useJobs`, `useJob`, `useRetryJob`, `useCancelJob`.
- Agents: `useTokens`, `useCreateToken`, `useDeleteToken`, `useAgentSetup`.
- Events: `useJobEvents` (mounted once in AppShell).

`useCurrentBrain()` from `src/app/context.ts` exposes `brainId`, the selected
`brain`, the latest 100 deduplicated typed `events`, and `connected`. Graph workers
should animate new `page_written` events using this feed and respect reduced motion.
Native EventSource reconnects with Last-Event-ID; reconnects and events invalidate
Brain content queries so missed changes are recovered. Changing Brain closes the
old stream. Job lists also poll as a fallback. SSE authentication failures are
checked with an authenticated API request to trigger the login gate.

Query keys use a resource prefix followed by Brain id and filters: e.g.
`['sources', brainId, folder, recursive]`, `['page', brainId, path]`,
`['graph', brainId]`, `['search', brainId, query, types, limit]`.
Keep these prefixes for SSE invalidation. Mutations invalidate affected queries.

`api` is the `openapi-fetch` client. `result(api.GET(...))` unwraps typed data or
throws `ApiError`, whose `status` and generated `body` preserve conflict details.
All non-GET requests include `X-SBB-Client: web`. Cookies stay same-origin.
Only 401 `login_required` replaces the current screen with Login; login restores
that route. Logout and session expiry clear protected query data. Credentials
and newly created API tokens are never persisted to browser storage.

`uploadSource` is the progress-capable XHR transport with the same CSRF/error
contract; it accepts `AbortSignal`. `useUploadSource` wraps it with invalidation.
`fileUrl` and `originalUrl` build encoded same-origin download URLs.

## Shared presentation

Use `Button` (variants and `asChild`), `Input`, `Select`, `Field`, `Dialog`,
`ConfirmDialog`, `TypeBadge`, `StatusBadge`, `Loading`, `ErrorNotice`, and `QueryError`
from `src/components/ui`. Dialogs use Radix focus management, Escape handling, and
focus return. `Field` links the label and hint to its input.

`DropZone` handles Source upload progress, queued success, duplicate links,
pending-job links, quota confirmation and explicit retry, and unmount cancellation.
`ProviderForm` and `GitHubConnect` are shared by onboarding and Settings. Device
polling follows server intervals and slowdown responses and stops on denial,
expiry, connection, or unmount. `CopyButton` reports clipboard failures.

`SafeMarkdown` uses react-markdown and remark-gfm without raw HTML. Internal links
route through the app, local Assets use `/api/brains/<id>/files/...`, and remote
images are suppressed. Wiki-specific citation hover cards belong in the Wiki feature.

Use `t(key, values)` for every UI string. The Paper/Ink tokens and page-type colors
are in `src/styles.css`, alongside Tailwind v4 theme mappings. `useTheme()` exposes
`theme` and `setTheme('paper' | 'ink')`; only the theme preference uses localStorage.
The sidebar, command palette, and Activity rail are responsive, keyboard reachable,
and respect reduced motion. The rail starts collapsed below 1200px.

## Verification scope

Vitest covers typed request bodies and headers, structured auth errors, optional
login and session expiry, private/public Brain onboarding, Provider creation and
skipping, device polling/slowdown/expiry, task settings, one-time tokens, multipart
progress/quota/duplicate conflicts, SSE cleanup/invalidation, safe Markdown, and
keyboard search navigation. Tests use generated API types with isolated mock HTTP,
XHR, and EventSource transports; no GitHub or Provider credentials are needed.
