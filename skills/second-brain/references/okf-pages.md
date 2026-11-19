# OKF pages in a Brain

Brains follow **Open Knowledge Format (OKF) v0.3**. Only the `okf` module in second-brain-builder validates the strict profile; agents mainly need types, links, and citations.

## Page types

| `type` | Location | Role |
|--------|----------|------|
| `source` | `sources/.../<slug>/<slug>.md` | Canonical Document: conversion of one Source; `resource` points at the original file |
| `summary` | `wiki/summaries/<slug>.md` | Main points of one Source (slug matches Source id) |
| `entity` | `wiki/entities/` | Named things |
| `concept` | `wiki/concepts/` | Ideas, methods, terms |
| `comparison` | `wiki/comparisons/` | Side-by-side comparisons |
| `synthesis` | `wiki/syntheses/` | Saved answers (`save_answer`) |

`index.md` (root) lists pages by section; `log.md` records changes. Neither is a wiki `type`. `AGENTS.md` and `CLAUDE.md` have no `type`.

## Wiki frontmatter (typical)

```yaml
type: entity
title: Example Person
description: One line; appears in index.md
tags: [tag]
sources:
  - id: example-report          # Source slug
    resource: /sources/reports/example-report/example-report.md
    title: Example Report
generated:
  by: sbb/<model>
  at: "2026-01-01T12:00:00+00:00"
```

Every footnote label in the body must appear in `sources`, and every `sources` entry must be cited in the body.

## Links

- Use standard Markdown links with paths from the repository root: `[Title](/wiki/entities/example.md)`.
- No `[[wikilinks]]`.
- Canonical Documents link assets as `assets/<name>` relative to the Source directory.

## Canonical Document (source type)

```yaml
type: source
title: Example Report
description: PDF, 10 pages.
resource: ./example-report.pdf
generated:
  by: "process:sbb-conversion"
  at: "2026-01-01T12:00:00+00:00"
```

Conversion output is faithful Markdown; ingest jobs add Wiki Pages, not rewrites of the Canonical Document.

## Slugs and folders

- Slugs: lowercase ASCII, digits, hyphens; unique per Brain.
- `sources/<folder>/` groups Sources; folders can nest.
- Reserved names (`index`, `log`, `assets`) are avoided in user slugs.