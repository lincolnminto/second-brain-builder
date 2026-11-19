# CLAUDE.md

This repository is public. Write every file as if a stranger will read it.

## Privacy

Personal data and secrets stay out of the repository. A leak in a public repo is permanent: forks, caches, and git history keep it.

- Keep secrets in `.env`. Document each variable in `.env.example` with a placeholder value.
- Keep real notes, exports, and other personal data in `private/`.
- Keep personal rules and local development notes in `CLAUDE.local.md`.
- Use synthetic data in tests, fixtures, examples, and docs: placeholder names, `example.com` emails, relative paths.
- When you add a new location that can hold personal data, add it to `.gitignore` in the same change.
- gitleaks scans every commit through pre-commit. Install the hook once per clone: `uv tool install pre-commit && pre-commit install`.
- If gitleaks blocks a commit, remove the secret from the change. Never bypass the hook (`--no-verify`, `SKIP=gitleaks`). If you think the finding is a false positive, ask the user.
- Before each commit, read the staged diff. Look for names, emails, tokens, absolute paths that contain a username, and real note content.

## Project map

- Use the domain terms in `GLOSSARY.md` in code, docs, and UI text.
- `docs/architecture.md` is the contract between modules. Read it before you change a module boundary, a public interface, the Brain repo layout, a job kind, or the API, and update it in the same change.

## Language

Use English for all project content: code, comments, docs, commit messages, issues, and pull requests.

## Communication

Use ASD-STE100 Simplified Technical English when you communicate with the user. Use short sentences, active voice, and consistent terms. Keep code identifiers, API names, and quoted source text exact. State the result and any limits clearly.

## Code standards

- Keep public APIs typed and compatible with Python 3.10+.
- Use `uv add` or project-local dependency patterns when dependencies change.
- Add focused tests for behavior changes. Regenerate reference data only when conversion outputs intentionally change.
- Prefer structured models over loose dictionaries for durable schema-like data. Use Pydantic models or dataclasses when data crosses module boundaries, is serialized, or represents a stable contract. Exceptions may apply for internal datatypes and trivial data structures.
- Prefer `pathlib.Path` for path-handling code. Use `Path` operations instead of `os.path` in new or edited code unless an API explicitly requires string paths.
- Avoid `hasattr(...)`, broad `getattr(...)`, and similar attribute-probing patterns. These usually hide interface uncertainty. If such a check is genuinely required for compatibility with a documented third-party API, keep it narrowly scoped and explain it in a comment.
- Write tests that verify meaningful application behavior, regressions, or integration boundaries. Skip trivial or self-validating tests: tests that restate well-established library behavior, or implementation details added only to validate the agent's own change. Avoid mock-heavy tests unless mocking is the clearest way to exercise a real contract or failure mode.

## When making changes

- Keep edits scoped and consistent with the surrounding module.
- Update docs/examples when user-facing behavior changes.
- Run targeted tests for touched behavior.

## Commits

- One short subject line in the imperative mood: `Add note parser`, `Fix tag export`.
- State what changed, nothing more. Keep the subject under 72 characters.
- Put the reason behind an important decision in an ADR, not in the commit.

## Decisions (ADRs)

Write an ADR only when the reason behind a decision is strictly important: the decision is hard to reverse, surprising, or has trade-offs a future contributor must know.

- Path: `docs/adr/NNNN-short-title.md`, numbered in sequence.
- Sections: Context, Decision, Consequences. Keep each one short.
