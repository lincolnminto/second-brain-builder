# Local Brain clone (no MCP)

Use a clone when MCP tools are not configured but the user has a git remote for their Brain.

## Clone

```bash
export GIT_LFS_SKIP_SMUDGE=1
git clone <clone-url> ~/second-brain/<brain-slug>
cd ~/second-brain/<brain-slug>
git lfs install --skip-smudge
```

With `GIT_LFS_SKIP_SMUDGE=1`, files under `sources/` are small LFS pointer files. Read the matching Canonical Document (`sources/.../<slug>/<slug>.md`) for full text.

To download one original Source later:

```bash
git lfs pull --include="sources/<folder>/.../<slug>/<slug>.<ext>"
```

## Where paths come from

The second-brain-builder UI **Agent setup** shows the MCP URL, skill install command, and suggested clone commands for each Brain. `SBB_PUBLIC_URL` defaults to `http://localhost:8484` in local Compose.

## Read order

1. `index.md` — all pages by type with one-line descriptions.
2. Pages from search or from links in those pages.
3. `log.md` — recent adds, ingests, moves, deletes, saved answers (newest first).

## Writes

The agent does not commit in the clone. The user adds Sources and saves answers through MCP or the web UI; the worker commits to GitHub.