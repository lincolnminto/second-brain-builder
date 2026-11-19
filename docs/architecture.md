# Architecture

This document is the contract between modules. Read it before you change a module boundary, a public interface, the Brain repo layout, a job kind, or the API. Terms follow [GLOSSARY.md](../GLOSSARY.md). Decisions with history live in [docs/adr/](adr/).

`second-brain-builder` and the `sbb` / `SBB_` prefixes are working names. A rename before launch is a mechanical search and replace.

## Runtime

Two containers share three volumes.

| Container | Runs | Image contents |
|---|---|---|
| `app` | HTTP API (`/api`), MCP server (`/mcp`), static web UI (`/`) | Python, git, git-lfs |
| `worker` | Every job (see [Jobs](#jobs)) | Python, git, git-lfs, ffmpeg, Calibre, Docling |

| Volume | Mount | Content |
|---|---|---|
| `data` | `/data` | `sbb.sqlite` (main database), `search.sqlite` (FTS index), `secret.key`, `uploads/` (staged uploads) |
| `brains` | `/brains` | One git clone per Brain: `/brains/<brain-id>/` |
| `models` | `/models` | Docling and Whisper models, downloaded on first use |

Rules:

- The `app` container never writes to a Brain clone. It reads clones and the database, stages uploads, and enqueues jobs. The `worker` does every write.
- Jobs that write to the same Brain run one at a time. Jobs for different Brains can run in parallel.
- One worker process runs the jobs of a database. It can run several jobs at a time in threads (`SBB_WORKER_CONCURRENCY`).
- GitHub holds the source of truth for Brain content ([ADR 0002](adr/0002-github-repo-is-brain-source-of-truth.md)). The database holds settings, credentials, jobs, events, and caches that the worker can rebuild from the clones.
- Compose publishes the UI on `127.0.0.1:8484` only.
- CPU is the default. The `gpu` Compose profile runs a CUDA build of the worker instead.
- Images run Python 3.12. Code stays compatible with Python 3.10+.

## Code layout

```
src/second_brain_builder/
  config.py      Settings from SBB_* environment variables
  domain.py      Cross-module types: ids, enums, small value models
  okf/           OKF v0.3: frontmatter models, page read/write, links, index.md, log.md, validation
  brains/        Brain repo: layout, slugs, scaffold, git + LFS operations, page reading, graph
  github/        Device flow, PAT, token refresh, user, installations, repos
  conversion/    Source → Canonical Document body + Assets (Docling, Calibre, media)
  providers/     LLM Provider clients (OpenAI- and Anthropic-compatible), transcription
  ingest/        LLM agent loop that writes Wiki Pages; removal; saved answers
  search/        SQLite FTS5 index over Canonical Documents and Wiki Pages
  store/         Database models, migrations, credential encryption, job queue, events
  services/      Use cases shared by api and mcp (thin adapters call these)
  api/           FastAPI app, routers, response schemas, auth, static UI
  mcp/           MCP server tools
  server.py      Production app: create_server() = api + mcp (uvicorn --factory)
  worker/        Job runner and one handler per job kind ("python -m second_brain_builder.worker")
web/             React UI
skills/second-brain/SKILL.md
docker/          Dockerfiles
compose.yaml
tests/<module>/  Tests mirror the package layout
```

Dependency direction. Arrows point from a module to what it may import. No cycles.

```
okf        → (nothing internal)
brains     → okf
search     → okf, brains
providers  → (nothing internal)
conversion → (nothing internal)
github     → (nothing internal)
ingest     → okf, brains, search, providers
store      → domain
services   → store, brains, search, github, providers, okf
worker     → store, brains, conversion, ingest, search, github, providers, okf
api, mcp   → services
```

`config` and `domain` can be imported by every module. Heavy dependencies (Docling, torch) are imported only inside `conversion`, and only in functions that run in the worker, so the `app` image does not need them.

## Brain repo layout

```
<brain-repo>/
├── AGENTS.md                 How agents read this Brain (not an OKF page: no `type`)
├── CLAUDE.md                 One line: @AGENTS.md
├── index.md                  Root OKF index (only file with okf_version frontmatter)
├── log.md                    OKF log, newest first
├── .gitattributes            LFS rules (see below)
├── .brain/brain.yaml         Brain settings (OKF ignores dot-paths)
├── sources/<folder>/.../<slug>/
│   ├── <slug>.<ext>          Source (Git LFS)
│   ├── <slug>.md             Canonical Document (type: source)
│   └── assets/               Images, <slug>.vtt, other Assets
└── wiki/
    ├── summaries/<slug>.md   type: summary (one per Source, same slug)
    ├── entities/<slug>.md    type: entity
    ├── concepts/<slug>.md    type: concept
    ├── comparisons/<slug>.md type: comparison
    └── syntheses/<slug>.md   type: synthesis (saved answers)
```

- Slugs: lowercase ASCII, digits, and `-`; built from the title or filename with Unicode folded to ASCII; a collision appends `-2`, `-3`, ... Folders are slug paths and can nest.
- `index`, `log`, and `assets` are reserved slugs: `slugify` gives `index-1`, `log-1`, `assets-1`. OKF reserves `index.md` and `log.md` at every level, and `.gitattributes` reads every `assets/` directory under `sources/` as Assets.
- If the Source is itself Markdown (`.md`), its file is `<slug>.original.markdown`. It does not collide with the Canonical Document, OKF does not read it as a page (it does not end in `.md`), and it goes to Git LFS like every other Source.
- A Source's id is its slug. Slugs are unique per Brain across all Folders, so a move never changes the id.
- `.gitattributes` (`!attr` makes an attribute unspecified, so Canonical Documents and Assets diff and merge as text; `-diff -merge` would make them binary):
  ```
  sources/** filter=lfs diff=lfs merge=lfs -text
  sources/**/*.md !filter !diff !merge text
  sources/**/assets/** !filter !diff !merge
  ```
- `.brain/brain.yaml`: `okf_version: "0.3"`, `schema_version: 1`, `name`, `language` (BCP 47, default `en`), `created_at`.
- Commits in a Brain repo use the connected GitHub user's noreply address (`<id>+<login>@users.noreply.github.com`), never a real email. Messages are short: `Add source: Dune`, `Ingest: Dune (12 pages)`, `Move source: Dune`, `Delete source: Dune`, `Save answer: <title>`.

## OKF rules

Brains are strict OKF v0.3 bundles ([ADR 0001](adr/0001-strict-okf-v0-3.md)). The `okf` module is the only code that knows the spec.

- Frontmatter is YAML 1.2 (use `ruamel.yaml`, never PyYAML, which is YAML 1.1). Unknown keys are kept on round-trip, also inside `sources` entries, `generated`, and `verified`. Anchors, aliases, duplicate keys, and nesting deeper than 32 levels are errors.
- Links are standard Markdown links. Code writes `/`-rooted paths: `[Paul Atreides](/wiki/entities/paul-atreides.md)`. Relative paths are valid OKF v0.3 (§6), and code resolves and rewrites them too: a Canonical Document links its Assets as `assets/<name>`. No `[[wikilinks]]`.
- Our extension keys use the prefix `x-sbb-`. Validation warns only about extension keys without `x-`, because other producers' keys (`x-acme-...`) are valid OKF.
- Timestamps are RFC 3339 with an offset. Code writes them as double-quoted strings (YAML 1.2 has no timestamp type) and reads both quoted and unquoted forms.
- Brains use a strict profile: `sources[].id` and `generated.at` are required, although OKF makes them optional. A page without them does not parse.
- Validation warns about a `.md` file that has frontmatter but no `type`. A file without frontmatter gets no warning, because every Brain has `AGENTS.md` and `CLAUDE.md` on purpose. Symbolic links are skipped.

Canonical Document frontmatter:

```yaml
type: source
title: Dune
description: EPUB, 412 pages.            # built from metadata, no LLM
resource: ./dune.epub                     # the Source file, relative
generated: {by: "process:sbb-conversion", at: "2026-11-18T12:00:00+00:00"}
x-sbb-source:
  original_filename: Dune - Frank Herbert.epub
  sha256: <hex>
  media_type: application/epub+zip
  size_bytes: 1234567
  converter: docling 2.150.1
  added_at: "2026-11-18T12:00:00+00:00"
```

Wiki Page frontmatter and citations:

```yaml
type: entity                              # summary | entity | concept | comparison | synthesis
title: Paul Atreides
description: Protagonist of Dune.          # one line; index.md uses it
tags: [character]
sources:
  - id: dune                              # Source slug
    resource: /sources/books/dune/dune.md # Canonical Document
    title: Dune
generated: {by: "sbb/<model>", at: "2026-11-18T12:00:00+00:00"}
```

Body claims cite with a footnote whose label is the source id: `... heir of House Atreides.[^dune]`, with `[^dune]: [Dune](/sources/books/dune/dune.md)` at the end. Every label used must be in `sources`, and every entry in `sources` must be cited.

`index.md` and `log.md` are written by code, never by the LLM:

- `index.md`: root frontmatter (`okf_version: "0.3"`, `title`, `description`, then any other keys of the old `index.md`, which OKF §8.1 says to keep), then one `#` section per type in this order: Sources, Summaries, Entities, Concepts, Comparisons, Syntheses. Empty sections are left out. Each entry: `* [Title](/path.md) - description`. Sorted by title.
- `log.md`: starts with `# Log`, then `## YYYY-MM-DD` headings, newest first; entries like `* **Ingest**: [Dune](/sources/books/dune/dune.md) - 12 pages updated`. Kinds: Create, Add, Ingest, Move, Delete, Reconvert, Answer.

## Module contracts

Signatures are the public surface. Internals are free. All public functions and models are typed. `StrEnum` is `domain.StrEnum`, a `(str, Enum)` whose `str()` gives the value on every Python version.

### okf

```python
OKF_VERSION = "0.3"
class OKFError(ValueError)                # frontmatter that is not a YAML mapping, has duplicate keys,
                                          # a known key of the wrong shape, or is not UTF-8
class PageType(StrEnum): SOURCE, SUMMARY, ENTITY, CONCEPT, COMPARISON, SYNTHESIS   # .is_wiki_page
class SourceRef(BaseModel): id: str; resource: str; title: str | None; author: str | None
    extensions: dict[str, Any]            # every other key of the entry
class Stamp(BaseModel): by: str; at: datetime; extensions: dict[str, Any]
class Frontmatter(BaseModel): type: str   # "" when the file has frontmatter without `type`
    title: str | None; description: str | None; resource: str | None
    tags: list[str]; sources: list[SourceRef]; generated: Stamp | None; verified: list[Stamp]
    status: str | None; superseded_by: str | None; stale_after: datetime | None
    extensions: dict[str, Any]            # every other key, round-tripped
class Page(BaseModel): path: PurePosixPath; frontmatter: Frontmatter | None; body: str = ""
class Issue(BaseModel): path: PurePosixPath; code: str; message: str; severity: Literal["error", "warning"]

def parse_page(path: PurePosixPath, text: str) -> Page
def render_page(page: Page) -> str
def read_page(root: Path, path: PurePosixPath) -> Page
def write_page(root: Path, page: Page) -> None
def is_concept(page: Page) -> bool                     # has a `type` and is not index.md or log.md
def extract_links(body: str) -> list[str]              # skips code spans and blocks
def resolve_link(from_path: PurePosixPath, href: str) -> PurePosixPath | None
def rewrite_links(body: str, moves: dict[PurePosixPath, PurePosixPath],
                  from_path: PurePosixPath | None = None) -> str
def remove_links(body: str, targets: Iterable[PurePosixPath], from_path: PurePosixPath | None = None) -> str
def apply_moves(path: PurePosixPath, moves: Mapping[PurePosixPath, PurePosixPath]) -> PurePosixPath
def bundle_href(path: PurePosixPath) -> str            # "/wiki/x.md", percent-encoded
def is_external(href: str) -> bool
def footnote_labels(body: str) -> set[str]
def cited_labels(body: str) -> set[str]                # footnote references only, not definitions
def remove_citations(body: str, source_id: str) -> str
def render_index(title: str, description: str, pages: list[Page], previous: str | None = None) -> str
class LogKind(StrEnum): CREATE, ADD, INGEST, MOVE, DELETE, RECONVERT, ANSWER
def log_entry(kind: LogKind, title: str, path: PurePosixPath | None = None, detail: str | None = None) -> str
def prepend_log(text: str, day: date, entries: list[str]) -> str
def parse_timestamp(value: str) -> datetime
def validate_page(page: Page, existing_paths: set[PurePosixPath]) -> list[Issue]
def validate_bundle(root: Path) -> list[Issue]
```

Also public: `Timestamp`, `IssueCode`, `Severity`, `WIKI_PAGE_TYPES`, `RESERVED_FILENAMES`, `SECTION_TITLES`, `LOG_TITLE`.

- `Page.path` is relative and has no `..`. `read_page` and `write_page` raise `ValueError` for a dot segment or a path that goes through a symbolic link. `parse_page`, `read_page`, and `render_index` (for `previous`) raise `OKFError` for frontmatter they cannot read; no other exception type comes from bad frontmatter.
- `render_page` raises `ValueError` for a page without frontmatter whose body starts with a frontmatter block: OKF cannot express that file.
- `write_page` writes a temporary `.okf-<hex>.tmp` in the target directory, then renames it, so readers see the old page or the new one.
- `rewrite_links` keys can be files or directories, so `source.move` passes `{old_source_dir: new_source_dir}`. Without `from_path` only `/`-rooted hrefs change. With `from_path` (the page path before the move), relative hrefs change too, also when the page itself moved. `remove_links` turns inline links and images to `targets` into their text (alt text).
- `render_index(previous=...)` takes the current `index.md` text and keeps its other frontmatter keys.

### brains

```python
class BrainSettings(BaseModel): okf_version: str = "0.3"; schema_version: int = 1; name: str
    language: str = "en"; created_at: datetime     # one-line non-empty name, BCP 47 language, UTC offset required
class GitIdentity(BaseModel): name: str; email: str
class GitAuth(BaseModel): token: SecretStr                # sent via http.extraheader from env, never argv/config/URL
class PageSummary(BaseModel): path: PurePosixPath; type: str; title: str; description: str | None
class GraphNode(BaseModel): path: PurePosixPath; type: str; title: str
class GraphEdge(BaseModel): from_path: PurePosixPath; to_path: PurePosixPath
class Graph(BaseModel): nodes: list[GraphNode]; edges: list[GraphEdge]    # both sorted by path
class GitError(RuntimeError); class GitConflictError(GitError)          # rebase conflict; the rebase was aborted
class BrainPathError(ValueError); class BrainSettingsError(ValueError)

def slugify(text: str) -> str
def is_slug(text: str) -> bool                            # False for reserved slugs
def unique_slug(base: str, taken: set[str]) -> str
class BrainLayout:                                        # pure path logic, no constructor arguments
    def source_dir(self, folder: PurePosixPath, slug: str) -> PurePosixPath
    def canonical_path(self, folder: PurePosixPath, slug: str) -> PurePosixPath
    def source_file_path(self, folder: PurePosixPath, slug: str, ext: str) -> PurePosixPath
    def assets_dir(self, folder: PurePosixPath, slug: str) -> PurePosixPath
    def wiki_path(self, type: PageType, slug: str) -> PurePosixPath
class GitRepo:
    def __init__(self, root: Path, identity: GitIdentity, auth: GitAuth | None) -> None
    def clone(self, url: str, skip_lfs_smudge: bool = False) -> None
    def pull_rebase(self) -> None
    def commit_all(self, message: str) -> str | None       # None when nothing changed
    def push(self, retries: int = 3) -> None               # on rejection: pull_rebase, retry
    def lfs_pull(self, paths: list[PurePosixPath]) -> None
    def lfs_usage_bytes(self) -> int
def scaffold_brain(root: Path, settings: BrainSettings) -> None
def read_settings(root: Path) -> BrainSettings
def write_index(root: Path) -> None                       # index.md from brain.yaml and read_pages
def read_pages(root: Path) -> list[Page]                  # every OKF page with its body, sorted by path
def list_pages(root: Path) -> list[PageSummary]
def build_graph(root: Path) -> Graph
def resolve_in_brain(root: Path, path: str) -> Path        # path from the Brain root; one leading "/" also means the root
```

Also public: `WIKI_DIRS`, `SOURCES_DIR`, `WIKI_DIR`, `ASSETS_DIR_NAME`, `SETTINGS_PATH`, `INDEX_PATH`, `LOG_PATH`, `MARKDOWN_SOURCE_SUFFIX`, `RESERVED_SLUGS`, `FALLBACK_SLUG`, `SCHEMA_VERSION`, `DEFAULT_BRANCH`, `GITATTRIBUTES`, `INDEX_DESCRIPTION`, `lfs_available()`.

- Git errors never include tokens. `AGENTS.md` and `CLAUDE.md` come from templates in `brains/templates/`.
- `BrainLayout` methods raise `ValueError` for a folder part or slug that is not a slug. The empty folder `PurePosixPath()` puts a Source directly under `sources/`.
- `read_pages`, `list_pages`, and `build_graph` skip dot-paths, all symbolic links, files in the `assets/` directory of a Source, names that are not UTF-8, and files whose frontmatter cannot be read (with a warning). A page without a title uses its filename stem. Graph edges include citations and leave out self-links, Assets, missing files, and external URLs.
- `resolve_in_brain` accepts `wiki/x.md` and `/wiki/x.md` for the same file; a host absolute path never leaves the root. It raises `BrainPathError` for an empty path, the root itself, `//`, `..`, a dot segment (also after symbolic links resolve), a backslash, NUL, or a symbolic link loop. Code that gets a path from a request, an MCP tool, or an OKF link uses it.
- `scaffold_brain` raises `FileExistsError`, and writes nothing, when a file it writes already exists. It does not commit. `write_index` keeps the description and other keys of the old `index.md`, and replaces frontmatter it cannot read. The worker calls it after each job. Brain files are written atomically (a `.sbb-<hex>.tmp` file, then a rename).
- `GitRepo.clone` refuses URLs with credentials and `<transport>::<address>` URLs. It clones with `GIT_LFS_SKIP_SMUDGE=1`, runs `git lfs install --local` (with `--skip-smudge` when `skip_lfs_smudge`), then `git lfs pull` when downloads are wanted. Without git-lfs it logs a warning and Sources then go to plain git. An empty remote gives branch `main`. If a step after the checkout fails, it removes what it wrote, so a retried job can clone again.
- Every git command gets its config from `GIT_CONFIG_COUNT` variables: the extraheader (only with `auth`), an empty `credential.helper`, and `commit.gpgSign=false`. Inherited variables that redirect git or inject config (`GIT_DIR`, `GIT_CONFIG_PARAMETERS`, ...) are dropped. `GitAuth` refuses tokens with spaces or control characters.
- `commit_all` never stages the temporary files of an interrupted write (`.okf-*.tmp`, `.sbb-*.tmp`). `push` retries only when the remote is ahead (`fetch first`, `non-fast-forward`): `retries=3` gives up to 4 attempts. `lfs_usage_bytes` counts each LFS object reachable from any ref once, old versions included, from the pointer blobs (no git-lfs needed). `lfs_pull` refuses paths that git-lfs would read as patterns.

### github

```python
TokenKind = Literal["app", "pat"]
class TokenSet(BaseModel): kind: TokenKind; access_token: SecretStr; expires_at: datetime | None = None
    refresh_token: SecretStr | None = None; refresh_expires_at: datetime | None = None   # UTC; naive means UTC
    def expires_within(self, margin: timedelta, now: datetime | None = None) -> bool
class DeviceCode(BaseModel): device_code: str; user_code: str; verification_uri: str; expires_in: int; interval: int
class DevicePending / DeviceDenied / DeviceExpired                       # empty
class DeviceSlowDown(BaseModel): interval: int                           # new minimum seconds between polls
DevicePollResult = TokenSet | DevicePending | DeviceSlowDown | DeviceDenied | DeviceExpired
class GitHubUser(BaseModel): id: int; login: str; noreply_email: str
class RepoInfo(BaseModel): full_name: str; private: bool; clone_url: str; default_branch: str

def start_device_flow(client_id: str, *, transport: httpx.BaseTransport | None = None) -> DeviceCode
def poll_device_flow(client_id: str, device_code: str, *, interval: int = 5,
                     transport: httpx.BaseTransport | None = None) -> DevicePollResult
def refresh_token(client_id: str, tokens: TokenSet, *, transport: httpx.BaseTransport | None = None) -> TokenSet
class GitHubClient:
    def __init__(self, token: SecretStr, transport: httpx.BaseTransport | None = None, *,
                 kind: TokenKind | None = None) -> None   # None: "ghu_" prefix gives app, else pat
    kind: TokenKind                                 # read-only property
    def user(self) -> GitHubUser
    def list_repos(self) -> list[RepoInfo]          # app token: repos of the user's installations; PAT: repos it can access
    def get_repo(self, full_name: str) -> RepoInfo
    def create_private_repo(self, name: str, description: str) -> RepoInfo
def install_url(app_slug: str) -> str
```

Errors: `GitHubError` (`summary`, `status_code`, `github_message`) and its subclasses `GitHubConnectionError` (no response), `GitHubAuthError` (401), `GitHubPermissionError` (403), `GitHubNotFoundError` (404), `GitHubValidationError` (422), `GitHubRateLimitError` (429, or 403 with `x-ratelimit-remaining: 0` or `retry-after`; has `reset_at`), and `GitHubOAuthError` (`error`, `description`) with its subclass `DeviceFlowDisabledError`. `status_code` is `None` when no usable HTTP response arrived. No error chains an httpx or pydantic exception, and every error pickles.

- All models are frozen. Callers that hold a `TokenSet` pass `kind=tokens.kind` to `GitHubClient`.
- `poll_device_flow` uses `interval` only when a `slow_down` response has no `interval`; the new interval is then `interval + 5`.
- `refresh_token` raises `ValueError`, and sends no request, when the `TokenSet` has no refresh token.
- `GitHubClient` raises `ValueError` for an empty token or one with a character outside visible ASCII (0x21-0x7E). It does not strip the token. Services strip pasted input and map this error to an "invalid token" client error.
- `list_repos` sorts by `full_name` (case-insensitive) and removes duplicates. `get_repo` raises `ValueError`, and sends no request, unless each part of `owner/name` matches `[A-Za-z0-9_.-]+` and is not `.` or `..`.

### conversion

```python
class SourceMetadata(BaseModel): title: str | None; author: str | None; pages: int | None; duration_seconds: float | None
class ConversionOptions(BaseModel): models_dir: Path; asr_model: str = "turbo"
    transcriber: Transcriber | None = None          # domain.Transcriber
    max_media_seconds: float = 86400                # longer audio or video raises ConversionError
class ConversionResult(BaseModel): markdown: str; assets: list[Path]; metadata: SourceMetadata; converter: str; warnings: list[str]
class UnsupportedFormatError(Exception); class DrmProtectedError(Exception); class ConversionError(Exception)

def sha256_file(path: Path) -> str
def detect_media_type(path: Path) -> str
def convert_source(source: Path, assets_dir: Path, options: ConversionOptions) -> ConversionResult
```

- `markdown` links images as `assets/<name>`. The worker adds the frontmatter.
- `assets` are absolute paths inside `assets_dir`, sorted by name: only files that the Markdown references. They are written only after a successful conversion, and `assets_dir` is created only when there are Assets. Other files in `assets_dir` stay, so the worker removes the old Assets before `source.reconvert`.
- `converter` is a comma-separated list of `tool version`: `docling 2.150.1`, `calibre 7.6.0, docling 2.150.1`, `docling 2.150.1, whisper turbo`, `provider transcriber`; video adds `rapidocr 3.10.0`.
- Supported media types (others raise `UnsupportedFormatError`): PDF, DOCX, PPTX, XLSX, HTML, XHTML, EPUB, Markdown, plain text, CSV, PNG, JPEG, TIFF, BMP, WebP, GIF, MOBI/AZW3, WAV, MP3, M4A, AAC, Ogg, FLAC, MP4, AVI, QuickTime, Matroska, WebM.
- Docling runs as a library (not `docling-serve`). PDFs keep images (`ImageRefMode.REFERENCED`, `generate_picture_images=True`).
- AZW3/MOBI: Calibre `ebook-convert` to EPUB, then Docling. Files with DRM raise `DrmProtectedError`. Nothing in the code removes DRM.
- Audio: Docling ASR with Whisper (`asr_model`, checkpoints in `<models_dir>/whisper`), or `options.transcriber` when the user picks a Provider. The transcript is also written as `assets/<source stem>.vtt`, so the worker converts the Source at `<slug>.<ext>` to get `<slug>.vtt`. The Canonical Document starts with `Transcript with timings: [<slug>.vtt](assets/<slug>.vtt)`, and each transcript paragraph starts with `**HH:MM:SS**`.
- Video: the same transcript, plus one `## HH:MM:SS` section per frame at a scene change, with `![Frame at HH:MM:SS](assets/frame-NNNN.png)` and the OCR text of the frame in a fenced `text` block.
- `HF_HOME` is set to `models_dir` only if it is unset, and only before `huggingface_hub` is first imported, so the worker image sets `HF_HOME` itself. `HF_HUB_DISABLE_TELEMETRY` defaults to `1`.

### providers

```python
class ProviderKind(StrEnum): OPENAI = "openai"; ANTHROPIC = "anthropic"   # "-compatible": base_url is configurable
class ProviderConfig(BaseModel): kind: ProviderKind; base_url: str; api_key: SecretStr
class ToolSpec(BaseModel): name: str; description: str; parameters: dict[str, Any]   # JSON Schema
class ToolCall(BaseModel): id: str; name: str; arguments: dict[str, Any] = {}
class Message(BaseModel): role: Literal["user", "assistant", "tool"]; content: str | None = None
    tool_calls: list[ToolCall] = []; tool_call_id: str | None = None
class StopReason(StrEnum): END_TURN, TOOL_USE, MAX_TOKENS, REFUSAL
class ChatResponse(BaseModel): text: str | None = None; tool_calls: list[ToolCall] = []; usage: Usage
    stop_reason: str                                # a StopReason value; unknown values pass through
    def to_message(self) -> Message                 # the assistant message for the next turn
class ChatModel(Protocol):
    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int) -> ChatResponse
# In domain, re-exported here (store and conversion import only domain):
class Usage(BaseModel): input_tokens: int = 0; output_tokens: int = 0     # supports +
class Segment(BaseModel): start: float; end: float; text: str
class Transcriber(Protocol):                        # runtime-checkable
    def transcribe(self, audio: Path, language: str | None) -> list[Segment]

class ProviderError(Exception): status_code: int | None
class ProviderConnectionError / ProviderAuthError (401, 403) / ProviderNotFoundError (404)
    / ProviderRateLimitError (429 after every retry) / ProviderResponseError (ProviderError)

def make_chat_model(config: ProviderConfig, model: str, *, transport: httpx2.BaseTransport | None = None) -> ChatModel
def make_transcriber(config: ProviderConfig, model: str, *,
                     transport: httpx2.BaseTransport | None = None) -> Transcriber   # OpenAI-compatible only
def list_models(config: ProviderConfig, *, transport: httpx2.BaseTransport | None = None) -> list[str]
```

- The official SDKs use `httpx2` (the successor of `httpx`), so `transport` is an `httpx2.BaseTransport`; another transport raises `TypeError`. `make_transcriber` raises `ValueError` for an Anthropic-compatible Provider.
- Every failed call raises a `ProviderError`. `ProviderResponseError` means an answer in an unexpected format, or tool arguments that are not a JSON object. No SDK exception is chained, and no message contains the key.
- `ProviderConfig`: `base_url` must be an absolute `http://` or `https://` URL with a valid host and port; whitespace and a trailing `/` are removed, and the Anthropic client also removes a trailing `/v1`. `api_key` is stripped and can be empty (local servers; the clients then send `no-key`), but it cannot hold a space, a control character, or a non-ASCII character. Validation errors never show the input.
- `Message` checks its role: a `tool` message needs `tool_call_id`, and only an `assistant` message has `tool_calls`.
- For `max_tokens` and `refusal`, `tool_calls` is empty and `text` keeps the partial answer. Anthropic `stop_sequence` maps to `end_turn`, and `model_context_window_exceeded` to `max_tokens`.
- Retries: the SDKs run with `max_retries=0`; providers retries connection errors and HTTP 408, 409, 429, and 5xx up to 4 times, and honors `x-should-retry`, `retry-after-ms`, and `retry-after` (at most 60 s). A timeout is never retried. Timeouts: chat and transcription read 900 s, `list_models` 30 s, connect 10 s.
- `Transcriber.transcribe` takes a BCP 47 tag and sends only its language subtag (`pt-BR` becomes `pt`).

### ingest

```python
class IngestEvent(BaseModel): kind: Literal["progress", "page_written", "page_deleted"]; data: dict[str, Any]
class IngestStop(StrEnum): COMPLETED, TOKEN_BUDGET, PAGE_LIMIT, REFUSED, NO_SUMMARY
class IngestResult(BaseModel): written: list[PurePosixPath]; deleted: list[PurePosixPath]; usage: Usage
    stopped: IngestStop = IngestStop.COMPLETED
class InvalidAnswerError(ValueError): problems: list[str]

def ingest_source(root: Path, canonical_path: PurePosixPath, model: ChatModel, model_name: str,
                  language: str, max_tokens_budget: int, on_event: Callable[[IngestEvent], None], *,
                  index: SearchIndex | None = None, brain_id: str | None = None) -> IngestResult
def remove_source(root: Path, source_id: str, on_event: Callable[[IngestEvent], None]) -> IngestResult
def save_answer(root: Path, title: str, body: str, source_ids: list[str], description: str, tags: list[str],
                model_name: str | None) -> PurePosixPath
def split_into_chunks(body: str, max_chars: int = CHUNK_CHARS) -> list[Chunk]   # Chunk(text, headings)
MAX_PAGES_PER_SOURCE = 40; CHUNK_TOKENS = 8000; CHARS_PER_TOKEN = 4; CHUNK_CHARS = 32000
UNKNOWN_AGENT = "agent:unknown"     # generated.by of save_answer without a model name
```

- The loop sends the Canonical Document one chunk at a time, each in a new conversation with the finish notes of the earlier chunks. `split_into_chunks` packs heading sections while they fit; a long section is split at paragraphs, then lines, then inside a line; a short piece (at most 1/8 of `max_chars`) starts the chunk of the next piece. The first chunk also writes the summary page.
- `index` and `brain_id` come together or not at all (`ValueError`). The worker passes its shared `SearchIndex`, which the loop searches and updates after each write. Without them, each call builds a temporary index of the clone.
- `stopped`: only `completed` means that the whole Canonical Document was processed. `token_budget` (input plus output tokens reached `max_tokens_budget`), `page_limit` (40 pages written), `refused` (stop reason `refusal`), and `no_summary` (chunk 1 ended without the summary page) stop before the later chunks. The written pages stay, and the caller decides whether to commit. A Canonical Document without text gives `completed` with no pages.
- Tools for the LLM: `search`, `list_pages`, `read_page`, `write_page`, `finish`. `write_page` accepts only `wiki/<type-dir>/<slug>.md` with a valid type, validates OKF frontmatter and citations, and returns errors to the LLM to fix (`Error: ...`). The LLM cannot touch `sources/`, `index.md`, `log.md`, `AGENTS.md`, or dot-paths.
- `write_page` also refuses: type `synthesis` (saved answers belong to the user); the summary of another Source; an update that drops the citations of other Sources; an update of a page that the model did not read in an earlier answer of the current chunk; an update of a page longer than 40000 characters (`read_page` shows only its start); a body that ends inside a code block or HTML block; paths over 1024 bytes, names over 255 bytes, and non-summary slugs over 100 characters. `sources` and the footnote definitions come from the citations and the Canonical Documents; definitions in the body are replaced.
- Source content is untrusted data. The system prompt says so, and the tool limits above hold whatever the model asks. In each chunk message, only the Source id and the Canonical Document path are outside the random-marker data blocks (`SOURCE-FACTS`, `SOURCE-TEXT`).
- Event data (paths without a leading `/`): `progress` `{source, chunk (1-based), chunks}`; `page_written` `{path, title, type, links}`, where `links` are the bundle paths that the body links to, citations included; `page_deleted` `{path, title, type}`. `written` is in first-write order for an Ingest and sorted by path for a removal.
- `remove_source` is deterministic: it removes the citations and `sources` entries for the id, deletes Wiki Pages with no sources left and `wiki/summaries/<source_id>.md`, and turns inline links, images, and reference-style links to deleted pages and to the Source directory into plain text. It emits `page_written` for each page it changes.
- `save_answer` writes `wiki/syntheses/<slug>.md` (never replaces a page) and raises `InvalidAnswerError` when it cannot write the page, for example when a Source id is unknown or uncited.

### search

```python
class SearchHit(BaseModel): path: PurePosixPath; title: str; type: str; snippet: str; score: float
class SearchIndex:
    def __init__(self, db_path: Path) -> None
    def rebuild(self, brain_id: str, root: Path) -> None
    def update(self, brain_id: str, root: Path, paths: list[PurePosixPath]) -> None   # missing files are removed
    def query(self, brain_id: str, text: str, limit: int = 20, types: list[str] | None = None) -> list[SearchHit]
    def remove_brain(self, brain_id: str) -> None            # services call it when a Brain is disconnected
HIGHLIGHT_MARK = "**"; SCHEMA_VERSION = 2
```

FTS5 over title, description, tags, and body. User query text is escaped; it is never passed as raw FTS syntax. All terms must match, and the last term also matches as a prefix.

- Ranking: hits whose title contains the whole query come first, then the description, then the tags. BM25 (weights: title 10, description 5, tags 3, body 1) orders the hits inside each group. `score` sorts like the hits (higher is better); compare it only within one query.
- `snippet` is a one-line Markdown fragment around the best match, from the body, else the description. Page text is backslash-escaped, and each match is between `**`. Clients render it as Markdown and do not split it on `**`.
- `update` paths are relative to the Brain root: strip the leading `/` of an OKF href. An absolute or empty path, or one with `..`, raises `ValueError`. A path that is missing, cannot be read, or is not a page loses its row. `rebuild` and `update` raise `NotADirectoryError`, and change nothing, when `root` is not a directory.
- `limit < 1` raises `ValueError`. `types=None` means all types, and `types=[]` gives no hits.
- Each Brain has its own FTS5 tables, so one Brain's results never depend on another. When the stored schema version differs from `SCHEMA_VERSION`, the index is emptied, and each Brain needs a rebuild (`brain.sync`).

### store

SQLAlchemy 2.0 ORM, Alembic migrations in `store/migrations/`, SQLite in WAL mode with a busy timeout.

| Table | Key columns |
|---|---|
| `settings` | `key`, `value` (JSON) |
| `credentials` | `id`, `kind`, `ciphertext`, `created_at`, `updated_at` |
| `github_account` | `user_id`, `login`, `noreply_email`, `token_kind`, `credential_id`, `expires_at`, `connected_at` |
| `providers` | `id`, `name`, `kind`, `base_url`, `credential_id`, `created_at` |
| `task_settings` | `task` (`ingest`, `transcription`), `mode` (`local`, `provider`), `provider_id`, `model` |
| `brains` | `id` (uuid), `name`, `repo_full_name`, `language`, `status` (`creating`, `ready`, `error`), `created_at`, `last_synced_at` |
| `sources` | `id`, `brain_id`, `slug`, `folder`, `path` (the Source file), `sha256`, `title`, `media_type`, `size_bytes`, `status` (`queued`, `converting`, `converted`, `ingesting`, `ingested`, `failed`), `created_at`, `updated_at` |
| `jobs` | `id`, `brain_id`, `kind`, `payload` (JSON), `status`, `attempts`, `error`, `worker_id`, `created_at`, `started_at`, `finished_at`, `input_tokens`, `output_tokens` |
| `job_events` | `id`, `job_id`, `brain_id`, `created_at`, `kind`, `data` (JSON) |
| `api_tokens` | `id`, `name`, `token_hash` (SHA-256), `prefix`, `created_at`, `last_used_at` |

```python
def make_engine(settings: Settings) -> Engine
def sqlite_engine(path: Path) -> Engine                     # an engine with the store pragmas
def make_session_factory(engine: Engine, *, immediate: bool = True) -> sessionmaker[Session]
def immediate_transactions(engine: Engine) -> Engine        # transactions start with BEGIN IMMEDIATE
def migrate(engine: Engine) -> None                         # alembic upgrade head
class MigrationError(Exception)
class Crypto:
    def __init__(self, key: bytes) -> None                   # a Fernet key
    @classmethod
    def load_or_create(cls, settings: Settings) -> Crypto    # SBB_SECRET_KEY, else /data/secret.key (mode 0600)
    def encrypt(self, plaintext: str) -> bytes
    def decrypt(self, ciphertext: bytes) -> str
class SecretKeyError(ValueError); class DecryptionError(ValueError)
class Job(BaseModel): id; brain_id; kind: JobKind; payload; status: JobStatus; attempts; error; worker_id
    created_at; started_at; finished_at; input_tokens; output_tokens          # frozen; datetimes aware UTC
class JobEvent(BaseModel): id; job_id; brain_id; created_at; kind; data      # frozen
class JobNotFoundError(LookupError)
class JobStateError(Exception): job_id; status; operation; owner: str | None
class JobQueue:
    def __init__(self, engine: Engine) -> None
    def enqueue(self, kind: JobKind, brain_id: str | None, payload: dict[str, Any]) -> Job
    def claim(self, worker_id: str) -> Job | None            # oldest queued job whose Brain has no running job
    def complete(self, job_id: int, usage: Usage | None, *, worker_id: str) -> None
    def fail(self, job_id: int, error: str, usage: Usage | None = None, *, worker_id: str) -> None
    def cancel(self, job_id: int) -> None                     # queued jobs only
    def requeue_stale(self, max_attempts: int = 3) -> int     # running jobs left by a stopped worker
    def emit(self, job_id: int, kind: str, data: dict[str, Any]) -> None
    def events_since(self, after_id: int, brain_id: str | None = None, limit: int = 1000) -> list[JobEvent]
    def latest_event_id(self) -> int                          # where a new event stream starts
    def get(self, job_id: int) -> Job
    def list_jobs(self, brain_id: str | None = None, statuses: Collection[JobStatus] | None = None,
                  limit: int = 100) -> list[Job]              # newest first
```

ORM classes: `Base`, `SettingRow`, `CredentialRow`, `GitHubAccountRow`, `ProviderRow`, `TaskSettingRow`, `BrainRow`, `SourceRow`, `JobRow`, `JobEventRow`, `ApiTokenRow`.

- `Usage` is `domain.Usage`. `complete` and `fail` take the `worker_id` given to `claim`, and raise `JobStateError` if the job is not running or another worker runs it. `cancel` of a job that is not queued also raises `JobStateError`. An unknown id raises `JobNotFoundError`.
- `JobQueue` writes a `status` event (`{"status": ...}`, plus `error` on failure) for every status change, in the same transaction. Handlers do not emit status events.
- `requeue_stale` treats every running job as stale, so call it when the worker starts. A requeued job keeps its id and its place in the queue and gets `attempts + 1`; at `max_attempts` it fails instead. It returns the number of requeued jobs.
- `events_since(brain_id=None)` returns the events of every Brain and of jobs without a Brain.
- `make_session_factory` uses `BEGIN IMMEDIATE` by default, so a transaction that reads and then writes does not fail with `database is locked`. Use `immediate=False` only for read-only sessions. Never keep a transaction open across uploads, git, or HTTP calls. Sessions use `expire_on_commit=False`.
- `migrate` runs all pending revisions in one `BEGIN IMMEDIATE` transaction with foreign keys off, so the app and the worker can both call it at startup. If the result breaks a foreign key, it raises `MigrationError` and the database does not change. Revisions can use `op.batch_alter_table` and must not set `PRAGMA foreign_keys`.
- Schema rules: `sources`, `jobs`, and `job_events` rows are deleted with their Brain (`ON DELETE CASCADE`). `task_settings.provider_id` and `providers.credential_id` are `ON DELETE SET NULL`; `github_account.credential_id` is `ON DELETE RESTRICT`. Unique: `(sources.brain_id, sources.slug)`, `brains.repo_full_name`, `providers.name`, `api_tokens.token_hash`. Integer ids are never reused. Enum columns store the member value with no `CHECK` constraint, so a new member needs no migration. Naive datetimes are rejected.
- `Crypto.decrypt` raises `DecryptionError` for a damaged token or one made with another key; an invalid key raises `SecretKeyError`. The key file is created atomically, so concurrent first starts get the same key.

### services

Use cases that the API and the MCP server share. Each one validates input, reads clones or the database, and enqueues jobs. Examples: `connect_github_*`, `create_brain`, `add_source(brain_id, filename, stream, folder, confirm_over_quota)`, `move_source`, `delete_source`, `list_pages`, `read_page`, `graph`, `search`, `save_answer`, `create_api_token`. `add_source` streams the upload to `/data/uploads/<uuid>/`, computes SHA-256 while it streams, rejects duplicates in the same Brain, checks the LFS quota, and enqueues `source.add`. GitHub tokens are refreshed here when they expire (`TokenSet.expires_within`). `connect_github_pat` strips the pasted token and maps the `ValueError` of `GitHubClient` to an invalid-token client error, not HTTP 500. Disconnecting a Brain calls `SearchIndex.remove_brain`. Paths from requests and MCP tools go through `resolve_in_brain`.

## Jobs

| Kind | Payload | Steps |
|---|---|---|
| `brain.create` | `brain_id`, `repo_full_name`, `create_repo` | Create the private repo if asked, clone, scaffold, commit, push |
| `brain.sync` | `brain_id` | Pull, rebuild the `sources` cache and the search index |
| `source.add` | `brain_id`, `upload_path`, `original_filename`, `folder`, `sha256` | Convert, write Source + Canonical Document + Assets, update index/log, commit, push, reindex; then enqueue `source.ingest` if an ingest Provider is set |
| `source.ingest` | `brain_id`, `source_slug` | Ingest, update index/log, commit, push, reindex |
| `source.reconvert` | `brain_id`, `source_slug` | `lfs_pull` the Source, remove the old Assets, convert again, overwrite the Canonical Document and Assets, commit, push, reindex |
| `source.move` | `brain_id`, `source_slug`, `folder` | `git mv`, rewrite every link (`rewrite_links` with `{old_dir: new_dir}` and each page's old path) and `sources[].resource` (`apply_moves`) to the moved paths, update index/log, commit, push, reindex |
| `source.delete` | `brain_id`, `source_slug` | Remove the Source directory, `remove_source`, update index/log, commit, push, reindex |
| `answer.save` | `brain_id`, `title`, `body`, `source_ids`, `description`, `tags` | `save_answer`, update index/log, commit, push, reindex |

Every handler pulls with rebase first and pushes after its commit. "Update index/log" means `write_index` and `prepend_log`. Status flow: `queued → running → succeeded | failed | cancelled`. On start, the worker requeues jobs left `running` by a crash (`requeue_stale`); a job that reaches 3 attempts fails. `JobQueue` writes the `status` events. Handlers emit `progress`, `page_written` (`path`, `title`, `type`, `links`), and `page_deleted`, with the data of the `IngestEvent`s. The UI graph grows live from `page_written` events. `source.ingest` passes the shared `SearchIndex` and the Brain id to `ingest_source`, and logs `IngestResult.stopped`.

## HTTP API

All routes are under `/api`. Response models live in `api/schemas.py`, and the OpenAPI document is at `/api/openapi.json`. The web UI generates its TypeScript types from it.

| Area | Routes |
|---|---|
| Health and setup | `GET /health`, `GET /setup` (GitHub connected, Brain count, Provider count) |
| Auth | `GET /auth/status`, `POST /auth/login`, `POST /auth/logout` |
| GitHub | `POST /github/device/start`, `POST /github/device/poll`, `POST /github/pat`, `GET /github/account`, `DELETE /github/account`, `GET /github/repos`, `GET /github/install-url` |
| Providers | `GET/POST /providers`, `PATCH/DELETE /providers/{id}`, `POST /providers/{id}/test`, `GET /providers/{id}/models`, `GET/PUT /settings/tasks` |
| Brains | `GET/POST /brains`, `GET/DELETE /brains/{id}` (delete disconnects; it never deletes the GitHub repo), `POST /brains/{id}/sync`, `GET /brains/{id}/lfs-usage` |
| Sources | `GET /brains/{id}/folders`, `GET/POST /brains/{id}/sources` (multipart upload), `GET/DELETE /brains/{id}/sources/{slug}`, `GET /brains/{id}/sources/{slug}/original`, `POST /brains/{id}/sources/{slug}/move`, `POST /brains/{id}/sources/{slug}/reconvert`, `POST /brains/{id}/sources/{slug}/ingest` |
| Pages | `GET /brains/{id}/pages`, `GET /brains/{id}/pages/{path}`, `GET /brains/{id}/files/{path}`, `GET /brains/{id}/graph`, `GET /brains/{id}/search?q=` |
| Jobs and events | `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/retry`, `POST /jobs/{id}/cancel`, `GET /events` (Server-Sent Events, `?brain_id=&after=`) |
| Agents | `GET/POST /tokens`, `DELETE /tokens/{id}`, `GET /agent-setup` (MCP URL, skill install command, clone commands) |

## MCP server

Mounted at `/mcp` (Streamable HTTP). Every call needs an API token (`Authorization: Bearer sbb_...`). Tools: `list_brains`, `search(brain, query, limit)`, `read_page(brain, path)`, `add_source(brain, filename, content_base64, folder)`, `save_answer(brain, title, body, source_ids, description, tags)`. Write tools enqueue jobs and return the job id.

## Agent skill

`skills/second-brain/SKILL.md` follows the open Agent Skills standard. It tells the agent to use the MCP tools when they are available. If they are not, it reads a local clone (default `~/second-brain/<brain-slug>`, cloned with `GIT_LFS_SKIP_SMUDGE=1`), starting with `index.md`. The agent cites Sources and never commits to a clone; writes go through MCP.

## Web UI

React, Vite, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, React Router, `openapi-fetch` with types generated from the OpenAPI document. The built UI is static files that the `app` container serves.

Screens: onboarding (connect GitHub → create or pick a Brain → add a Provider, which can be skipped → drop the first file); Brain home (Folder tree, drop zone, Sources with live job status, activity feed); Source view (Canonical Document, original download, citing Wiki Pages, jobs); Wiki Page view (rendered Markdown, footnote citations, type, tags, and sources); live graph of Wiki Pages; search palette (Ctrl/Cmd+K); settings (GitHub, Providers, task models, API tokens and agent setup). Light and dark themes. Strings sit in one catalog so translation is possible later.

The UI renders Markdown without raw HTML. Document content is untrusted.

## Security and privacy

- **Host check:** requests with a `Host` header outside `SBB_ALLOWED_HOSTS` get HTTP 400. This blocks DNS rebinding.
- **CSRF:** every non-GET request under `/api` needs either a Bearer token or the header `X-SBB-Client: web`. No CORS headers are sent.
- **Optional login:** if `SBB_PASSWORD` is set, the UI needs a login. The session is a signed cookie: `HttpOnly`, `SameSite=Strict`.
- **API tokens:** format `sbb_<random>`. Only the SHA-256 hash is stored, and the token is shown once. `/mcp` always needs a token.
- **Credentials:** encrypted with Fernet. They are never logged, never placed in git argv, config, or remote URLs, and never written to a Brain repo. The UI shows only the last four characters.
- **Uploads:** at most `SBB_MAX_UPLOAD_BYTES` (default 2 GiB). Every path from a request is resolved inside the Brain root, or the request is rejected.
- **Untrusted content:** documents are untrusted data. Ingest tools limit what the LLM can write, and the UI renders no raw HTML.
- **Telemetry:** none.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SBB_DATA_DIR` | `/data` | Database, key, uploads, search index |
| `SBB_BRAINS_DIR` | `/brains` | Brain clones |
| `SBB_MODELS_DIR` | `/models` | Model cache (`HF_HOME` points here) |
| `SBB_SECRET_KEY` | (generated) | Fernet key for credentials |
| `SBB_PASSWORD` | (unset) | Enables UI login |
| `SBB_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Host header allowlist |
| `SBB_PUBLIC_URL` | `http://localhost:8484` | Base URL shown in agent setup |
| `SBB_GITHUB_CLIENT_ID` | (project GitHub App) | Device flow client id |
| `SBB_GITHUB_APP_SLUG` | (project GitHub App) | Install URL |
| `SBB_LFS_QUOTA_BYTES` | 10 GiB | Upload warning limit |
| `SBB_MAX_UPLOAD_BYTES` | 2 GiB | Upload hard limit |
| `SBB_INGEST_MAX_TOKENS` | 400000 | Token budget per Source |
| `SBB_WORKER_CONCURRENCY` | 1 | Parallel jobs in the worker |

## Testing

- `pytest`. Tests use real git with local bare repos, never GitHub.
- GitHub HTTP calls are tested at the contract level with `httpx.MockTransport`. Provider calls use `httpx2.MockTransport` (the SDKs use httpx2), plus a real local HTTP server on 127.0.0.1 for what a mock transport skips, such as header values. Ingest is tested with a scripted `ChatModel`.
- Conversion tests run real Docling on small synthetic fixtures that `tests/conversion/conftest.py` builds. Tests that need ML models (PDF layout, ASR) are marked `slow`.
- Fixtures contain only synthetic or public-domain text.
- CI runs ruff, mypy, the fast suite on Python 3.10 and 3.12, the web build, and gitleaks.
