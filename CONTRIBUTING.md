# Contributing to Second Brain Builder

Documentation fixes, reproducible bug reports, and focused code changes are welcome. For a large change, open a feature request first so maintainers can discuss the scope with you.

Read [CLAUDE.md](CLAUDE.md) for project rules, [GLOSSARY.md](GLOSSARY.md) for domain terms, and [docs/architecture.md](docs/architecture.md) for module contracts. Use English in code, comments, documentation, issues, pull requests, and commit messages. Follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

You need Python 3.10+, uv, Git, Git LFS, Node 22.12+ or 24, and pnpm. Conversion also needs ffmpeg, Calibre (`ebook-convert`), and the OpenMP runtime (`libgomp1` on Debian/Ubuntu). CI uses Python 3.10 and 3.12, Node 24, and pnpm 12.15.0.

From the repository root:

```sh
uv sync --frozen --extra worker
pnpm --dir web install --frozen-lockfile
uv tool install pre-commit
pre-commit install
```

The `worker` extra installs conversion dependencies, including CPU PyTorch wheels. The pre-commit hook runs gitleaks. Never bypass the hook to commit a secret.

For a running instance, use the [README quick start](README.md#quick-start) and [container guide](docker/README.md). For frontend development, [web/README.md](web/README.md) documents the dev server, backend proxy, and generated API types. Instance configuration is in the [architecture guide](docs/architecture.md#configuration).

## Checks

Run the checks that match your change. These commands mirror CI and run from the repository root:

```sh
uv run --frozen --extra worker ruff check .
uv run --frozen --extra worker ruff format --check .
uv run --frozen --extra worker mypy
uv run --frozen --extra worker pytest -m 'not slow'
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
pre-commit run --all-files
```

Tests marked `slow` need ML models or large downloads. Run them when your change affects model-backed conversion and your machine has enough resources:

```sh
uv run --frozen --extra worker pytest -m slow
```

If you change HTTP API schemas, regenerate and include the API files:

```sh
pnpm --dir web gen:api
```

Use focused tests for behavior changes. For documentation-only changes, check commands, links, formatting, and claims against the source. State which checks you ran and any limits in your pull request.

## Privacy and example data

Use **synthetic data only** in fixtures, screenshots, examples, bug reports, and pull requests. Do not attach real notes, document exports, credentials, tokens, private repository names, or logs with personal content. Reproduce a failure with a small synthetic Source.

Keep local notes and documents under the ignored `private/` directory. Keep secrets in ignored local configuration. Never include secrets in an issue or pull request. If you add a new place that can hold personal data, add it to `.gitignore` in the same change. Review the staged diff for secrets and personal data before each commit.

Use [private vulnerability reporting](SECURITY.md) for security issues.

## Scope and commits

- Keep each pull request focused on one change. Explain the problem, the resulting behavior, and how you checked it.
- Keep Python APIs compatible with Python 3.10+. Follow the existing module boundaries and terminology.
- Update `docs/architecture.md` when you change a module boundary, public interface, Brain repository layout, job kind, or API.
- Write an ADR only for an important decision with lasting trade-offs. Follow `docs/adr/NNNN-short-title.md` with Context, Decision, and Consequences sections.
- Use one short commit subject in the imperative mood, under 72 characters: `Add note parser` or `Fix tag export`. State what changed. Put important architectural reasons in an ADR.

Before requesting review, check the diff, include relevant documentation, and describe any remaining risks or checks you could not run.
